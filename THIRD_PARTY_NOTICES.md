# 第三方组件说明

Windows 软件包包含 Python、Tcl/Tk 运行时，以及 requests、urllib3、certifi、charset-normalizer、idna、PyCryptodome、Pillow、NumPy、OpenCV、psutil、pywinauto、pywin32、comtypes 等组件，使用 PyInstaller 构建。

发布工具会从构建环境收集已安装发行包中的 LICENSE、COPYING、NOTICE 文件至 `third_party_licenses/`，并记录组件版本。第三方许可证不因本项目打包而改变。

微信、Wireshark 和 Npcap 不在软件包内，需用户自行安装并遵守各自条款。默认不嵌入 Node.js；JavaScript 签名兜底需要本机可执行 `node`。如果构建时使用 `--include-node`，维护者还需一并提供所嵌入 Node.js 版本及其依赖的完整许可文件。

`wechat_assets/` 中的图像是用于识别界面控件的局部截图，不代表微信或图书馆官方授权、背书或隶属关系。
