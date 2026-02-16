# Cooldown Reader

A Python desktop app that reads MMO action bar cooldowns via screen capture and image analysis.

## What It Does

- Captures a specific screen region (your action bar) at high frame rates
- Detects which abilities are **ready** vs **on cooldown** via brightness analysis
- Shows a transparent overlay rectangle so you can calibrate the capture region
- Displays live slot states in a control panel
- (Future) Reads countdown numbers via OCR
- (Future) Sends keypresses based on priority rules

## Setup

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows

# Install dependencies
pip install -r requirements.txt
```

## Run

```bash
python -m src.main
```

## Usage

1. Launch the app — you'll see the control panel and a green overlay rectangle on screen
2. Adjust **Top / Left / Width / Height** until the overlay lines up with your action bar
3. Set the **Slot count** to match your action bar
4. Make sure all abilities are off cooldown, then click **Calibrate Baselines**
5. Click **Start Capture** — the live preview and slot states will update in real-time

## Project Structure

```
cooldown-reader/
├── .cursorrules          # AI coding context (for Cursor IDE)
├── requirements.txt
├── config/
│   └── default_config.json
└── src/
    ├── main.py           # Entry point, wires everything together
    ├── capture/          # Screen capture (mss)
    ├── analysis/         # Slot detection + OCR
    ├── overlay/          # Transparent calibration overlay
    ├── ui/               # PyQt6 control panel
    ├── automation/       # (Future) Key sending
    └── models/           # Data structures
```

## Requirements

- Python 3.11+
- Game running in **borderless windowed** mode (overlay can't appear over exclusive fullscreen)
- OS: Windows, Linux, macOS (overlay behavior may vary)
