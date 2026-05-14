工具集是一组用于支持和简化 InSAR 处理工作流的工具。


### 选择配对

根据时间和垂直基线标准，从 ASF 搜索结果中选择干涉图配对。

```python
from insarhub import Downloader
from insarhub.utils import select_pairs

s1 = Downloader.create('S1_SLC',
                    intersectsWith=[-113.05, 37.74, -112.68, 38.00],
                    start='2020-01-01',
                    end='2020-12-31',
                    relativeOrbit=100,
                    frame=466,
                    workdir='path/to/dir')
results = s1.search()

pairs, baselines, scene_bperp, _ = select_pairs(search_results=results)
```

::: insarhub.utils.select_pairs
    options:
        members: false
        heading_level: 0


### 绘制配对网络

绘制由 `select_pairs` 返回的 SBAS 干涉图网络。

```python
from insarhub.utils import plot_pair_network

fig = plot_pair_network(pairs=pairs, baselines=baselines, scene_baselines=scene_bperp)
fig.show()
```

示例：

![networks](../quickstart/fig/ifgs_network.png){:  margin: auto;" }


::: insarhub.utils.plot_pair_network
    options:
        members: false
        heading_level: 0

### ERA5 下载器

下载用于 MintPy 大气延迟校正的 ERA5 压力层天气数据。自动从 HyP3 zip 文件中确定所需的获取日期和空间范围，并使用 MintPy 兼容命名规则（`ERA5_S*_N*_W*_E*_YYYYMMDD_HH.grb`）保存文件。需要包含 [CDS API](https://cds.climate.copernicus.eu/api-how-to) 凭据的 `~/.cdsapirc` 文件。

```python
from insarhub.utils import ERA5Downloader

era5 = ERA5Downloader(output_dir='path/to/era5', num_processes=3, max_retries=3)
era5.download_batch(batch_dir='path/to/hyp3/outputs')
```

::: insarhub.utils.ERA5Downloader
    options:
        members:
            - download_batch
        heading_level: 0

### Earthdata 凭据池

如果用户拥有多个 Earthdata 凭据，可将其存储在 `~/.credit_pool` 中，格式为：
```bash
username1:password1
username2:password2
```
然后读取使用：
```python
from insarhub.utils import earth_credit_pool
ec_pool = earth_credit_pool()
```
可将其传入处理器，实现多个 Earthdata 凭据之间的无缝切换：

```python
from insarhub import Processor
processor= Processor.create('Hyp3_S1', earthdata_credentials_pool=ec_pool, ....)
```

::: insarhub.utils.earth_credit_pool
    options:
        members: false
        heading_level: 0

### SLURM 任务配置

此类封装了生成 SLURM 批处理脚本所需的所有参数，包括资源分配、任务设置、环境配置和执行命令。

```python
from insarhub.utils import Slurmjob_Config
config = SlurmJobConfig(
            job_name="my_analysis",
            time="02:00:00",
            command="python analyze.py"
        )
config.to_script("analysis.slurm")
```

::: insarhub.utils.Slurmjob_Config
    options:
        members: false
        heading_level: 0
