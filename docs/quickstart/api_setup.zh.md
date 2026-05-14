
本程序需要以下几个 API 账号才能完整使用，所有账号均可免费注册。

#### [NASA Earthdata](https://urs.earthdata.nasa.gov/)

此账号用于搜索卫星场景、下载 DEM 和轨道文件，以及提交在线干涉图处理任务。

注册完成后，在主目录下创建（如不存在）名为 `.netrc` 的文件，并添加：

```bash
machine urs.earthdata.nasa.gov
    login 您的Earthdata用户名
    password 您的Earthdata密码
```

`或者`

程序在首次使用时会自动提示登录。<br><br>



#### [Copernicus Data Space Ecosystem](https://dataspace.copernicus.eu/)

此账号用于下载轨道文件。CDSE 发布轨道文件的时间比 ASF 早几小时至几天。如果 CDSE 不可用或返回错误，InSARHub 将自动切换到 ASF 下载轨道文件。

注册完成后，在主目录下创建（如不存在）名为 `.netrc` 的文件，并添加：

```bash
machine dataspace.copernicus.eu
    login 您的CDSE用户名
    password 您的CDSE密码
```

`或者`

程序在首次使用时会自动提示登录。<br><br>

#### [Copernicus Climate Data Store](https://cds.climate.copernicus.eu/)

此账号用于使用 PyAPS 进行大气延迟校正。

注册完成后，在主目录下创建（如不存在）名为 `.cdsapirc` 的文件，并添加您的 [API Token](https://cds.climate.copernicus.eu/how-to-api)：

```bash
url: https://cds.climate.copernicus.eu/api
key: 您的个人访问令牌
```
