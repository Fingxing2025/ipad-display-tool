# iPad 副屏设置

<img src="assets/icon.png" alt="iPad 副屏图标" width="128">

一个用于 **GNOME Wayland + Sunshine + Moonlight** 的中文桌面工具：把 iPad 当作电脑的独立扩展屏幕，并管理分辨率、刷新率、缩放和屏幕位置。

<img src="docs/screenshot.png" alt="iPad 副屏设置窗口" width="520">

## 功能

- 分辨率预设和自定义宽高。
- 刷新率可输入 **1–360 Hz**，支持两位小数，例如 59.94、75、144、165；实际串流帧率取决于 Moonlight 设置和设备能力。
- 副屏缩放随分辨率提供可用比例；保留电脑主屏的缩放、模式和主屏标记。
- 把副屏放在主屏左、右、上、下方，按缩放后的逻辑尺寸排列。
- 一键启动、关闭、重新授权，显示等待授权、可以连接和已连接等状态。
- 使用专属应用图标，可创建桌面快捷方式。
- 重复启动同一组参数时保留现有虚拟屏幕和共享会话。

## 适用环境

目前在 **Ubuntu 26.04 / GNOME 50 / Wayland**，配合原生 Sunshine 和 iPad Moonlight 验证。依赖 Mutter 的虚拟屏幕接口和 GNOME 50 的缩放规则，其他桌面环境尚未适配。

请先安装原生 [Sunshine](https://docs.lizardbyte.dev/projects/sunshine/latest/) 并与 iPad 上的 [Moonlight](https://moonlight-stream.org/) 完成配对。工具使用 `app-dev.lizardbyte.app.Sunshine.service` 用户服务及 `~/.config/sunshine/` 配置，不适用于 Sunshine Flatpak。

Ubuntu 所需桌面组件可安装为：

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 \
  gir1.2-gstreamer-1.0 gstreamer1.0-pipewire libnotify-bin mutter-common-bin
```

Sunshine、显卡驱动和 Moonlight 需要单独安装。以同一普通桌面用户运行安装脚本，不要用 `sudo` 运行本工具。

## 安装

```bash
git clone https://github.com/Fingxing2025/ipad-display-tool.git
cd ipad-display-tool
/usr/bin/python3 install.py --desktop
```

去掉 `--desktop` 可仅安装应用列表入口。`--dry-run` 列出安装文件，`--check` 只检查运行依赖。

安装时复制程序和图标、注册用户服务、在 Sunshine 应用列表中添加“iPad 副屏”。已有 Sunshine 应用、配对信息和密码会保留。被替换的文件会备份到 `~/.local/state/ipad-display/install-backups/`。安装本身不会关闭当前副屏或启动串流。

## 使用

1. 打开应用列表中的 **iPad 副屏设置**。
2. 选择分辨率、缩放、刷新率和位置，点击 **应用并启动**。
3. 系统授权窗口出现后，选择 **Virtual remote monitor / 虚拟远程显示器**，点击 **共享**。
4. 在 iPad 的 Moonlight 中打开 **iPad 副屏**。

需要授权时由 Sunshine 自动弹出系统窗口；工具不能代替 GNOME 的授权确认。虚拟屏幕保持开启时会尝试复用已保存的授权；重建副屏后通常需要重新选择。

“仅保存参数”在下次启动时生效；修改参数或“重新授权”会重新创建副屏，当前串流需要重新连接。“关闭副屏”先停止 Sunshine，再移除虚拟屏幕。关闭设置窗口会让副屏继续运行。

### 缩放与清晰度

缩放比例越大，文字和窗口越大。系统允许的比例与分辨率有关：例如 2408×1506 支持 100% 和 200%；接近的 2400×1500 可使用 125%、150% 等比例。工具会列出可用选项。

Moonlight 的分辨率和帧率也需要与虚拟屏幕设置匹配。仅提高电脑端参数不会提升 iPad 本身的硬件刷新率。

### 连接与共享

电脑和 iPad 应能互相访问，例如处于同一局域网。工具首次启动时把 Sunshine 的捕获方式设为 `portal`。如果已有额外的 Sunshine 地址绑定或防火墙限制，仍需确保 iPad 可以访问该地址。

此版本的启动流程会在创建和摆放虚拟屏幕后重新初始化桌面共享组件，以适应本机 GNOME 50 的输出变化问题；这可能中断其他应用正在进行的屏幕共享。

## 文件位置

| 内容 | 位置 |
| --- | --- |
| 程序 | `~/.local/libexec/ipad-display-*.py` 和 `ipad_display.py` |
| 参数 | `~/.config/ipad-display/settings.json` |
| 操作日志 | `~/.local/state/ipad-display/control.log` |
| 虚拟屏幕服务 | `~/.config/systemd/user/ipad-virtual-display.service` |
| Sunshine 服务依赖 | `~/.config/systemd/user/app-dev.lizardbyte.app.Sunshine.service.d/ipad-display.conf` |

仓库不包含个人 Sunshine 配置、配对信息、授权令牌、运行日志和崩溃文件。

## 开发与验证

使用系统 Python 和 GI 包：

```bash
PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -v
/usr/bin/python3 -m compileall -q src install.py
```

测试覆盖屏幕布局保留、旋转和缩放后的几何关系、参数兼容与保存、错误共享来源、未完成授权等状态。真实设备串流仍需在 GNOME 桌面和 iPad 上验证。

## 许可证

本项目使用 [MIT 许可证](LICENSE)，允许使用、修改和分发。

## 参考

- [Mutter DisplayConfig 接口](https://gitlab.gnome.org/GNOME/mutter/-/blob/50.1/data/dbus-interfaces/org.gnome.Mutter.DisplayConfig.xml)
- [Mutter 50.1 缩放规则](https://github.com/GNOME/mutter/blob/50.1/src/backends/meta-monitor.c)
- [XDG Remote Desktop Portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html)
- [iPad Extended Display](https://github.com/theroryn/iPad-Extended-Display)：Mutter 虚拟屏幕与 Sunshine 配合方式的参考。
