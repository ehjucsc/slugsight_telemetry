# SlugSight Telemetry System

Complete rocket telemetry system with LoRa transmission, ground station, and dual-redundant data logging for rocket flight data acquisition.

## System Overview

This system consists of three main components:

1. **Transmitter (TX)** - Arduino/Feather M4 on the rocket (collects data, logs to SD, transmits via LoRa)
2. **Receiver (RX)** - Arduino receiver at ground station (receives LoRa, forwards to USB)
3. **Ground Station Software (GDS)** - Python software for real-time visualization and recording

### Data Flow

```
        [ROCKET]                                      [GROUND]
Sensors -> TX CPU -> SD Card (Backup Log)      RX Radio -> USB -> Python GDS -> CSV File (Primary Log)
             |                                     ^
             |                                     |
        LoRa Radio ------------------------> LoRa Radio
```

## Hardware Components

### Transmitter (On Rocket)
- **Microcontroller:** Adafruit Feather M4 Express
- **Radio:** RFM95W LoRa Radio (915 MHz)
- **Storage:** Micro SD Card Module (CS Pin 13) - *Provides onboard redundancy*
- **Sensors:**
  - LSM6DSOX IMU (Accelerometer/Gyroscope)
  - LIS3MDL Magnetometer
  - BMP280 Barometer
  - GPS Module (115200 baud)

### Receiver (Ground Station)
- **Microcontroller:** Arduino Uno/Nano (or compatible)
- **Radio:** RFM95W LoRa Radio (915 MHz)

## Telemetry Data (18 Fields)

The system transmits 17 fields from the rocket, and the receiver adds RSSI:

| # | Field | Unit | Description |
|---|-------|------|-------------|
| 1-3 | Pitch, Roll, Yaw | degrees | Orientation angles (Sensor Fusion) |
| 4 | Altitude | meters | Barometric altitude (MSL) |
| 5 | Velocity | m/s | Vertical velocity |
| 6-8 | Accel X, Y, Z | g | 3-axis acceleration |
| 9 | Pressure | Pa | Atmospheric pressure |
| 10 | IMU Temp | °C | IMU temperature |
| 11 | GPS Fix | 0/1 | GPS fix status |
| 12 | GPS Sats | count | Number of satellites |
| 13-14 | GPS Lat, Lon | degrees | GPS coordinates |
| 15 | GPS Altitude | meters | GPS altitude |
| 16 | GPS Speed | m/s | GPS ground speed |
| 17 | VBat | volts | Battery voltage |
| 18 | RSSI | dBm | Radio signal strength (added by RX) |

## Quick Start

### 1. Upload Firmware

**Transmitter:**
```bash
# Open firmware/slugsight_tx/slugsight_tx.ino in Arduino IDE
# Select board: Adafruit Feather M4 Express
# Upload to rocket Feather M4
```

**Receiver:**
```bash
# Open firmware/slugsight_rx/slugsight_rx.ino in Arduino IDE
# Select board: Arduino Uno (or your receiver board)
# Upload to receiver Arduino
```

### 2. Install Ground Station Software

It is recommended to use a virtual environment:

```bash
cd gds

# Create virtual environment
python -m venv venv

# Activate (macOS/Linux)
source venv/bin/activate

# Activate (Windows PowerShell)
.\venv\Scripts\Activate.ps1
# OR (Windows CMD)
venv\Scripts\activate.bat

# Install dependencies
pip install -r requirements.txt
```

### 3. Run Ground Station

```bash
cd gds
source venv/bin/activate  # Ensure venv is active
python slugsight_gds.py
```

Then open http://127.0.0.1:8080 in your web browser.

## Ground Station Features

- **Real-time Dashboard** - Live telemetry visualization
- **Automatic Data Logging** - All data saved to CSV files
- **Flight Metrics** - Max altitude, max G-force tracking
- **GPS Tracking** - GPS position and fix status
- **Battery Monitoring** - Battery voltage and percentage
- **Signal Quality** - RSSI monitoring

## Data Logging & Redundancy

This system implements a **Dual-Redundant** logging strategy to ensure no flight data is lost:

### 1. Ground Recording (Primary)
The Ground Station software automatically saves all received telemetry packets to CSV files on your laptop.
- **Location:** `gds/flight_data/`
- **Filename:** `slugsight_YYYYMMDD_HHMMSS.csv`

### 2. Onboard SD Card (Backup)
The transmitter (TX) writes every data packet to an onboard Micro SD card **before** transmission. This creates a complete, high-fidelity log of the flight even if the radio link cuts out or the ground station fails.
- **Location:** Root of SD card
- **Filename:** `LOGxx.CSV` (increments automatically)

**CSV Format (Both Logs):**
```csv
timestamp,packet_count,Pitch,Roll,Yaw,Altitude,Velocity,Accel X,Accel Y,Accel Z,Pressure Pa,IMU Temp C,GPS Fix,GPS Sats,GPS Lat,GPS Lon,GPS Alt m,GPS Speed m/s,VBat,RSSI
2025-11-09T12:00:00.123,0,5.2,-3.1,45.8,125.5,15.3,0.5,0.2,9.8,101325.0,22.5,1,8,37.123456,-122.345678,130.2,12.5,3.85,-95
...
```

## Configuration

### Transmitter Settings (`firmware/slugsight_tx/slugsight_tx.ino`)
- **Frequency:** 915 MHz (US) - Change `RF95_FREQ` for your region
- **Data Rate:** Bw500Cr45Sf128 (fastest)
- **TX Power:** 23 dBm (max)
- **Update Rate:** 10 Hz (100 Hz sensor fusion)

### Ground Station Settings (`gds/slugsight_gds.py`)
- **Serial Baud:** 115200
- **Output Directory:** `gds/flight_data/`
- **CSV Precision:** 6 decimal places
- **Buffer Size:** 10 packets (flush interval)

## Post-Flight Analysis

Use Python to analyze your flight data:

```python
import pandas as pd
import matplotlib.pyplot as plt

# Load flight data
df = pd.read_csv('gds/flight_data/slugsight_20251109_120000.csv')

# Plot altitude profile
plt.figure(figsize=(12, 6))
time_sec = df.index * 0.1  # 10 Hz data rate
plt.plot(time_sec, df['Altitude'])
plt.xlabel('Time (seconds)')
plt.ylabel('Altitude (meters)')
plt.title('Flight Altitude Profile')
plt.grid(True)
plt.show()

# Find apogee
apogee_idx = df['Altitude'].idxmax()
apogee_altitude = df.loc[apogee_idx, 'Altitude']
apogee_time = apogee_idx * 0.1
print(f"Apogee: {apogee_altitude:.1f}m at {apogee_time:.1f}s")

# Calculate max acceleration
max_g = df[['Accel X', 'Accel Y', 'Accel Z']].apply(
    lambda row: (row**2).sum()**0.5, axis=1
).max()
print(f"Max G-Force: {max_g:.1f}g")
```

## Directory Structure

```
slugsight_telemetry/
|-- README.md                      # This file
|-- .gitignore                     # Git configuration
|-- LICENSE-FIRMWARE               # GPLv3 License for Firmware
|-- LICENSE-SOFTWARE               # MIT License for GDS
|-- docs/                          # Documentation
|   `-- FLIGHT_DAY_GUIDE.md        # Operational guide
|-- firmware/                      # Arduino Code (GPLv3)
|   |-- slugsight_tx/              # Transmitter code (rocket)
|   `-- slugsight_rx/              # Receiver code (ground)
`-- gds/                           # Ground Data System (Python - MIT)
    |-- flight_data/               # Logged flight data
    |-- templates/                 # Web dashboard HTML
    |-- slugsight_gds.py           # Main application
    |-- telemetry_parser.py        # CSV parser module
    |-- data_logger.py             # Data logging module
    `-- requirements.txt           # Python dependencies
```

## Troubleshooting

See `docs/FLIGHT_DAY_GUIDE.md` for detailed troubleshooting steps.

## License

This project is dual-licensed:

1. **Firmware (`firmware/`)**: The Arduino transmitter and receiver code is licensed under the **GPLv3** (GNU General Public License v3.0) to comply with the RadioHead library dependency.
2. **Ground Station (`gds/`)**: The Python ground station software and documentation are licensed under the **MIT License**.

See `LICENSE-FIRMWARE` and `LICENSE-SOFTWARE` for full details.

## Support

For issues or questions, please open an issue on GitHub.

## Credits

Developed for the UCSC Rocket Team (SlugSight Avionics)
