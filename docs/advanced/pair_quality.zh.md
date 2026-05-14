# 配对质量评分

InSARHub 在网络选择前后对每个干涉图配对进行评分，以估计其可能的相干性。评分驱动网络编辑器中的颜色编码以及用于即时查询的预建配对数据库。

---

## 概述

质量评分为 **100** 表示该配对极有可能产生可用的干涉图；评分为 **0** 表示预计完全去相干。评分综合了物理信号 — 来自全球卫星数据集的预期相干性、积雪和降雨条件、季节以及土地覆盖 — 汇总为单一数值。

评分由 `PairQuality` 计算（针对选定配对），并由 `PairQualityDB` 存储所有 N×(N-1)/2 场景组合的评分至 `.insarhub_pair_quality_db.json`。

---

## 评分流程

```
FeatureAssembler.assemble()
        │
        ▼
  特征向量字典
        │
        ▼
  coherence_score()  ──► 第一级：S1 全球相干性（AWS S3）
        │                第二级：LC/NDVI 评分器（WorldCover）
        │                第三级：气候学安全线
        ▼
  score: int [0–100]
  factors: dict（惩罚项分解）
```

### 数据来源

| 信号 | 来源 | 备选方案 |
|--------|--------|----------|
| 预期相干性 | S1 全球相干性数据集，AWS S3（Kellndorfer et al. 2022） | 纬度带气候学表 |
| 积雪覆盖比例 | MODIS 日积雪产品 | 无（视为 0） |
| 降水量 | Open-Meteo 历史再分析 | 无（视为 0） |
| 土地覆盖比例 | ESA WorldCover 10m | 无（跳过分支混合） |
| NDVI | MODIS 16 天合成 | 气候学 |
| 火灾事件 | NASA FIRMS VIIRS（可选，需要 `FIRMS_MAP_KEY`） | 跳过 |

所有获取的数据均缓存在 `.insarhub_quality_cache.json` 中 — 历史数据不会改变，因此每个日期/位置只获取一次。

---

## 评分模式

### 第一级 — S1 全球相干性（主要）

当 AWS S3 数据集可访问时，基础信号为基于 Kellndorfer et al.（2022）全球季节性数据集的**预期干涉相干性 γ**。

逐像素衰减模型参数（γ∞、γ0、τ）按季节从 S3 COG 瓦片拟合，并缓存为 `decay_maps/` 中的 GeoTIFF。对于每个配对，使用实际时间基线评估模型：

```
γ(dt) = γ∞ + (γ0 − γ∞) · exp(−dt / τ)
```

**相干性惩罚** — 偏移二次函数，γ ≥ 0.60 时零惩罚，γ ≤ 0.10 时满惩罚：

```
clamped     = clamp((0.60 − γ) / 0.50, 0, 1)
coh_penalty = clamped²
```

**叠加的环境惩罚：**

| 惩罚 | 权重 | 饱和点 |
|---------|--------|-------------|
| 相干性 | 1.00 | γ ≤ 0.10 |
| 积雪（最差单指标） | 0.25 | 覆盖率 = 1.0 |
| D1 降水量 | 0.75 | 3天30mm |
| D2 降水量 | 0.75 | 3天30mm |
| 冻融交替 | 0.05 | — |

```
total_penalty = 1.00·coh + 0.25·snow + 0.75·pr_d1 + 0.75·pr_d2 + 0.05·ft
score = clamp(round((1 − total_penalty) × 100), 0, 100)
```

**强制为零 — 湿雪** → 评分直接为 0：

- 获取日温度 > 0°C 且 MODIS 积雪比例 > 30%
- C 波段在液态水含量 ≥1% 时穿透深度降至 5–10cm

### 第二级 — LC/NDVI（S3 不可用时）

当 S3 无法访问且有 WorldCover 土地覆盖数据时，使用**土地覆盖分支模型**。AOI 分为三类土地覆盖，各自评分后按面积比例混合：

| 分支 | 土地覆盖 | 关键驱动因素 |
|--------|-----------|------------|
| A — 城市/裸地 | 城市 + 裸地/岩石 | 几何基线，宽松时间惩罚 |
| B — 植被 | 农田 + 草地 + 灌木 | NDVI × 时间交互，季节跨越 |
| C — 森林 | 树木覆盖 + 红树林 | 相干性始终较低，上限 0.25 |

### 第三级 — 气候学安全线

当 S3 失效**且**无 NDVI/土地覆盖数据时，使用硬编码的纬度带相干性表作为基础信号，然后叠加与第一级相同的环境惩罚。此路径始终返回值 — 流程不会产生空评分。

---

## 年度重复奖励

时间基线在整数倍 365 天（最多 4 年）±20 天以内的配对可获得奖励，降低其惩罚。该配对在两个日期捕获相同的季节性植被和积雪状态 — 主要去相干来源相互抵消。

---

## 解读 `factors` 字典

每个已评分配对除评分外还存储一个 `factors` 字典：

```json
{
  "score": 82,
  "coherence_expected": 0.54,
  "coherence_source": "s3",
  "penalties": {
    "coherence":   0.08,
    "snow":        0.00,
    "precip_d1":   0.03,
    "precip_d2":   0.01,
    "freeze_thaw": 0.00
  },
  "hard_kill": null,
  "dt_days": 12,
  "bperp_diff": 8.3
}
```

---

## Python API

```python
from insarhub.utils.pair_quality import PairQuality

pq = PairQuality("/data/bryce/p100_f466")
result = pq.compute()

# result.scores  → {"scene_a:scene_b": 82, ...}   (int 0–100)
# result.factors → {"scene_a:scene_b": {...}, ...}

pq.print_summary()   # 打印排名表至标准输出
```

### 预建数据库（所有场景组合）

```python
from insarhub.utils.pair_quality._db import PairQualityDB

# 构建一次（在后台运行）
db = PairQualityDB("/data/bryce/p100_f466")
thread = db.precompute_background(scenes_by_stack, bperp_by_stack)

# 即时查询（无网络调用）
score = PairQualityDB.lookup(folder, ref_scene, sec_scene)   # float | None
status = PairQualityDB.status(folder)   # {exists, complete, n_scenes, n_pairs, built_at}
```

数据库在配对选择后新增场景时自动重建。当选择参数（`dt_max`、`pb_max` 等）改变时**不**重建，因为评分仅取决于场景日期、天气和相干性，与图算法无关。
