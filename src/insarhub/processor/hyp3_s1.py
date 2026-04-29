from colorama import Fore
from tqdm import tqdm

from insarhub.config import Hyp3_S1_Config
from insarhub.processor.hyp3_base import Hyp3Base 


class Hyp3_S1(Hyp3Base):
    name = "Hyp3_S1"
    description = "HyP3 InSAR GAMMA processing. Produces geocoded interferograms from Sentinel-1 SLC pairs."
    compatible_downloader = "S1_SLC"
    default_config = Hyp3_S1_Config
    def __init__(self, config: Hyp3_S1_Config | None = None):
        super().__init__(config)
        # Fetch InSAR specific cost table
        try:
            self.cost = self.client.costs()['INSAR_GAMMA']['cost_table'][f'{self.config.looks}']
        except Exception as e:
            print(f"{Fore.YELLOW}Warning: Could not fetch InSAR cost table ({e}). Using local cost table.{Fore.RESET}")
            if self.config.looks == "20x4":
                self.cost = 10
            elif self.config.looks == "10x2":
                self.cost = 15
            else:
                raise ValueError(f"{Fore.RED}Unsupported looks configuration: {self.config.looks}. Please provide a valid cost for this looks setting.{Fore.RESET}")
                
    def submit(self):
        """
        Submit InSAR jobs to HyP3 based on the current configuration.

        Prepares job payloads from the `pairs` in the configuration
        and submits them via `_submit_job_queue`, handling user rotation,
        batching, and credit checks.

        The job names are automatically generated using the `name_prefix`
        and scene IDs.

        Raises:
            ValueError: If `self.config.pairs` is not a tuple of two strings
                        or a list of tuples of two strings.

        Returns:
            dict:
                A dictionary mapping usernames to lists of submitted `Batch` objects.

        Example:
            ```python
            processor = Hyp3InSARProcessor(config)
            batches = processor.submit()
            for user, batch in batches.items():
                print(f"{user} submitted {len(batch)} jobs")
            ```
        """
        
        # Normalize pairs input
        if isinstance(self.config.pairs, (list, tuple)) and all(isinstance(p, str) for p in self.config.pairs):
                pairs = [(self.config.pairs[0], self.config.pairs[1])]
        elif isinstance(self.config.pairs, (list, tuple)) and all(isinstance(p, tuple) for p in self.config.pairs):
            pairs = self.config.pairs
        else:
            raise ValueError(f"{Fore.RED}Invalid pairs format. Provide a list of tuples or a tuple of two strings.\n")
        
        job_queue: list[dict] = []
        
        for (ref_id, sec_id) in pairs:
            # We use the client to help format the dict, but we don't submit yet.
            # We are preparing the payload for _submit_job_queue
            job = self.client.prepare_insar_job(
                granule1=ref_id,
                granule2=sec_id,
                name= f"{self.config.name_prefix}_{ref_id.split('_')[5]}_{sec_id.split('_')[5]}",
                include_look_vectors=self.config.include_look_vectors,
                include_inc_map = self.config.include_inc_map,
                looks = self.config.looks,
                include_dem=self.config.include_dem,
                include_wrapped_phase=self.config.include_wrapped_phase,
                apply_water_mask=self.config.apply_water_mask,
                include_displacement_maps=self.config.include_displacement_maps,
                phase_filter_parameter=self.config.phase_filter_parameter
            )
            job_queue.append(job)
            
        # Send to base class for batching and submission
        batchs = self._submit_job_queue(job_queue)
        self.batchs = batchs
        return batchs