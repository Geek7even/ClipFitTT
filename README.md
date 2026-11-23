# ClipFitTT

**ClipFitTT 是一款为方便在 TeamTalk 手机端预览视频而制作的 macOS 小工具。**
在日常沟通和协作中，它帮助你在电脑上提前整理需要分享的视频，减少手动调整格式和文件大小的重复操作。
通过 Finder 右键即可批量处理 MP4/MOV，将视频重新封装或压缩为 H.264 MP4，
控制单个文件小于十进制 **100 MB（100,000,000 字节）**，并保留原分辨率与同源帧率，方便后续发送和预览。

当前版本：**ClipFitTT Beta 0.9.2**（`0.9.2-beta.1`），发布日期 **2026-11-24**。
本项目为独立工具，与 TeamTalk、HandBrake、FFmpeg 无官方隶属或背书关系。

## 处理规则

| 源文件 | 处理方式 |
| --- | --- |
| 小于 100 MB，视频编码为 H.264 | FFmpeg 直接重新封装为 MP4，不重新编码视频 |
| 重新封装后达到或超过 100 MB | 自动转入 HandBrake 限大小转码 |
| 大于或等于 100 MB，或不是 H.264 | HandBrake x264 两遍平均码率转码，目标约 99.9 MB |

- 输出到源目录：`a.mp4` 或 `a.mov` → `a_compressed.mp4`。
- 重名自动编号：`a_compressed_2.mp4`；不覆盖已有输出或源文件。
- 转码不裁剪、不主动缩放、不设最高帧率；校验尺寸、帧数、时长、编码和音轨数。
- 重新封装保留视频数据及旋转信息。音轨优先复制；不兼容 MP4 时只将音频转为 AAC。
- HandBrake 转码保留音轨数量，音频转 AAC（通常每轨 128 kbps，最多立体声，长视频按预算降低）。不复制字幕轨。
- 多选顺序执行；多次启动的任务排队。单个文件失败后继续处理后面的文件。
- 本地暂存后复制至源目录，支持 SMB NAS；检查保存后的大小和 SHA-256，不依赖硬链接。
- 保存失败时清理不完整的输出，保留本地 `work/…/best.mp4` 供恢复。

转码属于有损压缩，保留尺寸和帧率不等于保留原画质；长视频会分配更低码率。
HandBrake 的同源 VFR 模式可能规整部分时间戳；重新封装不经过该帧率处理。
画面简单时可能用不满目标大小，不添加填充数据。最多四轮码率调整；无法满足上限时明确报错。
奇数尺寸、异常时间戳、受保护媒体或解码器不支持的输入可能被拒绝。

## 环境

- macOS；Apple Silicon 已实机验证，Intel 安装路径做了兼容处理，尚未实机验证。
- Python **3.11+**，使用标准库；无需 pip 安装 Python 包。
- 可执行的 `ffmpeg`、`ffprobe`。
- HandBrakeCLI **1.11.2+**；安装器会检查参数与预设兼容性。
- 首次下载 CLI 和检查更新需要连接 HandBrake 官方 GitHub 发布服务。

安装器不安装 Homebrew、不修改系统 Python、不自动安装 FFmpeg，也不更新 HandBrake 图形版。
如果使用 Homebrew，可先执行 `brew install python ffmpeg`；安装前确认 `python3 --version` 为 3.11+。

## 安装

下载并解压源码，或克隆到任意本地文件夹，然后在仓库根目录执行：

```sh
python3 install.py
```

安装器会检测 PATH、Apple Silicon/Intel 的常用 Homebrew 路径，以及本工具已经安装的 CLI。
若找不到兼容 CLI，会在交互终端询问是否从官方下载；默认不下载。
也可以明确授权下载，或指定已有程序：

```sh
python3 install.py --download-cli
python3 install.py --handbrake /path/to/HandBrakeCLI
```

`--download-cli` 只在没有兼容 CLI 时下载。已有较新版本会继续使用。
安装无需 `sudo`，工作流中的 Python 路径在本机生成，不包含发布者电脑路径。
已安装的日志和恢复文件保留；安装过程中替换受管理文件失败时会恢复原文件。

安装位置：

```text
~/Library/Services/ClipFitTT.workflow
~/Library/Application Support/ClipFitTT/
```

## 使用

Finder 中选择 MP4/MOV，右键点击 **ClipFitTT — 压缩视频至100MB**；macOS 也可能将它放在“服务”中。
终端显示进度和最终输出路径。等待“完成”后再使用输出；复制期间目标文件可能已经可见。
完成后按回车结束；Control+C 可取消任务。首次访问受保护文件夹时，macOS 可能请求正常的文件访问权限。

## 日志和 CLI 更新

日志在程序目录的 `logs/` 中，文件名不含源文件名，例如：

```text
2026-11-13_12-30-00__process__a1b2c3d4.log
2026-11-13_12-30-05__update__e5f6g7h8.log
```

时间为北京时间，精确到秒；随机标识避免同秒重名。每个源文件一份处理日志，一批任务结束后另有一份更新检查日志。
每个文件处理前，删除最后修改时间超过 7×24 小时的本工具日志，包括旧的 `encode-*.log`。

每批任务结束后只检查 HandBrakeCLI。发现新版时显示当前/新版本，询问 `[y/N]`；只有 `y`/`yes` 才更新。
网络失败不影响已完成视频。下载校验 SHA-256、版本和当前预设；更新前保留 `.previous` 备份，安装后版本校验失败时恢复旧版。
当前仓库不分发任何第三方二进制，依赖许可和来源见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

日志内容、生成配置、工作流和媒体元数据可能包含私人路径或标识；见 [PRIVACY.md](PRIVACY.md)。

## 卸载

删除本工具的上述 `.workflow` 和 `ClipFitTT` 程序目录，然后注销/登录刷新菜单。
删除程序目录前，先检查 `work/` 是否包含需要恢复的视频。FFmpeg、Python 和其他应用不受影响。

## 开发与验证

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 tools/check_release.py
```

单元测试不联网、不注册系统服务，也不需要真实客户媒体。
可选的本机安装/媒体集成测试只在临时目录生成合成视频，需本机 FFmpeg 和兼容 CLI：

```sh
python3 tools/smoke_test.py --handbrake /path/to/HandBrakeCLI
```

隔离安装示例（不覆盖正常安装、不注册 Finder 服务）：

```sh
python3 install.py --handbrake /path/to/HandBrakeCLI \
  --app-dir /tmp/clipfittt-test/App \
  --services-dir /tmp/clipfittt-test/Services --no-register
```

## 许可证与贡献

项目代码和文档采用 **GPL-2.0-only**，全文见 [LICENSE](LICENSE)，版权见 [COPYRIGHT](COPYRIGHT)。
`preset.json` 的上游来源、第三方许可及外部程序分发义务见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
贡献与发布流程见 [CONTRIBUTING.md](CONTRIBUTING.md)、[RELEASING.md](RELEASING.md)。

## 设计与开发

以下署名适用于本项目；HandBrake、FFmpeg 等第三方组件由各自项目维护。

**Designed by Geek7even**

**Developed entirely with AI**
