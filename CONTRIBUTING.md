# 参与开发

## 本地环境

使用 Windows x64 和 Python 3.13 创建虚拟环境，安装 `requirements.txt`，以 `python app_entry.py` 启动。不要提交个人生成的 `config.json`。

## 测试

```powershell
python -m unittest test_auth_refresh test_booking_recovery test_daily_schedule test_quick_book test_startup_booking test_wechat_navigation test_environment_setup
```

测试使用模拟接口和临时配置。不要在自动化测试中提交真实预约、取消预约或访问其他用户账号。

## 提交更改

说明触发场景、行为变化和验证方式。微信相关问题请提供版本、显示缩放、主题和已脱敏的错误描述。尽量通过系统控件识别定位界面；使用图片模板时只保留必要控件局部，不包含头像、昵称、聊天或身份信息。

## 发布检查

- 示例配置的 Cookie、代理、微信路径与候选座位为空；自动预约默认关闭。
- 不提交抓包、日志、授权回调、缓存、虚拟环境或构建产物。
- 运行完整测试和空白配置启动验证。
- 用 `build_software.py` 和 `package_release.py` 生成发布包，校验 SHA-256。
- 更新变更记录，保留项目与第三方许可说明。
