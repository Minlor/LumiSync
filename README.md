
<div align="center">

# 💡 LumiSync

**Sync your Govee LED strips with your screen or music**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://img.shields.io/pypi/v/lumisync.svg)](https://pypi.org/project/lumisync/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![GitHub stars](https://img.shields.io/github/stars/Minlor/LumiSync.svg?style=social)](https://github.com/Minlor/LumiSync/stargazers)

[Features](#features) • [Installation](#installation) • [Usage](#usage) • [Development](#development) • [Roadmap](#roadmap)

</div>

---

> [!NOTE]
> This project is in active development. Windows is fully supported; Linux X11 is partial, macOS/Wayland are WIP.

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🖥️ **Monitor Sync** | Sample colors from screen regions and sync to your LED strip in real-time |
| 🎵 **Music Sync** | React to audio with dynamic color patterns |
| 🎨 **Modern GUI** | PyQt6 interface with Windows 11-style navigation and theme support |
| 🔍 **Auto-Discovery** | Automatically finds Govee devices on your LAN via UDP broadcast |
| ⚡ **Low Latency** | Direct LAN communication, no cloud required |

<div align="center">
<img src="https://via.placeholder.com/700x400?text=LumiSync+Screenshot" alt="LumiSync Screenshot" width="700"/>
</div>

## 📦 Installation

**Requirements:** Python 3.11 or higher

### From PyPI (Recommended)

```bash
pip install lumisync
```

### From GitHub (Latest)

```bash
pip install git+https://github.com/Minlor/LumiSync.git
```

### Development Install

```bash
git clone https://github.com/Minlor/LumiSync.git
cd LumiSync
pip install -e .
```

## 🚀 Usage

### Launch the App

```bash
lumisync
```

Select option `3` to launch the GUI, or choose `1` (Monitor Sync) / `2` (Music Sync) for CLI mode.

### Quick Start

1. **Discover devices** - Click "Discover Devices" in the Devices tab
2. **Select your LED strip** - Click on the discovered device
3. **Start syncing** - Go to Sync Modes and click "Start Monitor Sync" or "Start Music Sync"

### Configuration

- **LED Mapping** - Customize which screen regions map to which LEDs
- **Brightness** - Adjust per-mode brightness (10-100%)
- **Display Selection** - Choose which monitor to capture (multi-monitor support)
- **Themes** - Switch between light/dark themes via Settings

## 🛠️ Development

### Project Structure

```
lumisync/
├── lumisync.py          # Entry point & CLI
├── connection.py        # Govee UDP protocol (port 4001/4002)
├── devices.py           # Device discovery & caching
├── config/options.py    # Runtime configuration
├── sync/                # Monitor & music sync engines
├── gui/                 # PyQt6 application
│   ├── controllers/     # Business logic (QObject + pyqtSignal)
│   ├── views/           # UI components
│   └── widgets/         # Reusable widgets
└── utils/               # Logging, colors, file ops
```

### Run Tests

```bash
python tests/test_color.py
```

### Platform Support

| Platform | Screen Capture | Status |
|----------|---------------|--------|
| Windows | dxcam | ✅ Full support |
| Linux (X11) | mss | ⚠️ Partial |
| Linux (Wayland) | - | 🚧 WIP |
| macOS | - | 🚧 WIP |

## 🗺️ Roadmap

- [ ] Multi-device support
- [ ] Wayland & macOS screen capture
- [ ] Basic color control mode
- [ ] Custom sync algorithms
- [ ] Plugin system for community extensions

## 🙏 Credits

- **[Wireshark](https://wireshark.org/)** — Protocol analysis
- See [pyproject.toml](pyproject.toml) for all dependencies

## 📄 License

[MIT](LICENSE) © Minlor

---

<div align="center">

**[minlor.net](https://minlor.net)** · **[GitHub @minlor](https://github.com/minlor)**

⭐ Star this repo if you find it useful!

</div>
