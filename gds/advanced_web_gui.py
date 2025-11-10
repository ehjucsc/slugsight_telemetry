import serial
import serial.tools.list_ports
import threading
import time
import json
import logging
from pathlib import Path
from flask import Flask, render_template_string, request
from flask_sock import Sock
from telemetry_parser import TelemetryParser
from data_logger import DataLogger

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Configuration ---
# This list finds your RECEIVER Arduino (Uno, Nano, etc.)
ARDUINO_VID_PIDS = [
    (0x2341, 0x0043),  # Official Arduino Uno R3
    (0x2341, 0x0001),  # Another Arduino ID
    (0x1A86, 0x7523),  # Common CH340 clone (Nano, etc.)
    (0x239A, 0x8022),  # Adafruit Feather M4 (if used as receiver)
]

# --- NEW 18-POINT DATA CONTRACT ---
# This list MUST match the data from the Receiver Arduino
DATA_LABELS = [
    "Pitch", "Roll", "Yaw",
    "Altitude", "Velocity",
    "Accel X", "Accel Y", "Accel Z",
    "Pressure Pa", "IMU Temp C",
    "GPS Fix", "GPS Sats",
    "GPS Lat", "GPS Lon", "GPS Alt m", "GPS Speed m/s",
    "VBat", "RSSI"  # <-- 17th and 18th points
]

# --- Global variables ---
global_data = {label: "0.0" for label in DATA_LABELS}
global_status = {
    "arduino_connected": False,
    "port": "Not Found"
}
data_lock = threading.Lock()
global_clients = []

# --- Initialize Telemetry Parser and Data Logger ---
parser_config = {
    'validation': {
        'enable_range_check': False  # Disable range checking for more permissive parsing
    }
}
telemetry_parser = TelemetryParser(parser_config)

# Data logger configuration
logger_config = {
    'output_directory': str(Path(__file__).parent / 'flight_data'),
    'filename_format': 'slugsight_%Y%m%d_%H%M%S',
    'auto_create_directory': True,
    'csv': {
        'delimiter': ',',
        'include_header': True,
        'float_precision': 6
    },
    'buffer_size': 10  # Flush to disk every 10 packets
}
data_logger = DataLogger(logger_config)
logger.info(f"Data will be logged to: {data_logger.get_current_file()}")

# --- Part 1: Serial Reader Thread ---

def find_arduino_port():
    """Finds the GCS Receiver Arduino port."""
    ports = serial.tools.list_ports.comports()
    for port in ports:
        for vid, pid in ARDUINO_VID_PIDS:
            if port.vid == vid and port.pid == pid:
                logger.info(f"Found GCS Receiver on port: {port.device}")
                return port.device
    return None

def serial_reader_thread(port):
    """
    Reads the 18-point CSV string from the GCS Receiver Arduino.
    Parses and logs telemetry data.
    """
    global global_data, global_status, global_clients
    while True:
        try:
            # Connect to the GCS Receiver Arduino
            with serial.Serial(port, 115200, timeout=1) as ser:
                logger.info(f"Serial connection to {port} established.")
                while True:
                    line = ser.readline()
                    if not line:
                        continue

                    try:
                        line_str = line.decode('utf-8').strip()
                        if not line_str:
                            continue

                        # Parse the telemetry using TelemetryParser
                        telemetry = telemetry_parser.parse(line_str)
                        
                        if telemetry:
                            # Log to CSV file
                            data_logger.write(telemetry)
                            
                            # Update global data for web display
                            payload = None
                            with data_lock:
                                global_status["arduino_connected"] = True
                                
                                # Convert telemetry dict back to display format
                                for label in DATA_LABELS:
                                    if label in telemetry:
                                        global_data[label] = str(telemetry[label])
                                
                                payload = json.dumps({
                                    "type": "update",
                                    "status": global_status,
                                    "data": global_data
                                })

                            # Push data to all clients
                            if payload:
                                dead_clients = []
                                for client in global_clients:
                                    try:
                                        client.send(payload)
                                    except Exception as e:
                                        dead_clients.append(client)
                                
                                if dead_clients:
                                    with data_lock:
                                        for client in dead_clients:
                                            if client in global_clients:
                                                global_clients.remove(client)

                    except UnicodeDecodeError:
                        pass
                    except Exception as e:
                        logger.error(f"Error processing telemetry line: {e}")

        except serial.SerialException as e:
            logger.error(f"Serial error: {e}")
            logger.info("Retrying in 5 seconds...")
            with data_lock:
                global_status["arduino_connected"] = False
            time.sleep(5)
        except Exception as e:
            logger.error(f"An unexpected error occurred: {e}")
            with data_lock:
                global_status["arduino_connected"] = False
            logger.info("Retrying in 5 seconds...")
            time.sleep(5)

# --- Part 2: Web Server (Flask) ---

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SlugSight Ground Station</title>
    
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&family=Roboto+Mono:wght@400;700&display=swap" rel="stylesheet">
    <link href="https://fonts.googleapis.com/icon?family=Material+Icons+Outlined" rel="stylesheet">
    
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns"></script>
    
    <style>
        :root {
            --font-family: 'Roboto', -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
            --font-family-mono: 'Roboto Mono', 'Courier New', Courier, monospace;
            --bg-color: #121212;
            --panel-bg: linear-gradient(145deg, #2c2c2c 0%, #1e1e1e 100%);
            --text-color: #ffffff;
            --text-secondary: #bbbbbb;
            --border-color: #333333;
            --shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
            --primary-color: #00bfff;
            --accent-color: #F5B81C;
            --purple-accent: #9370DB;
            --red-accent: #FF6347;
            --green-accent: #32CD32;
            --status-connected: var(--green-accent);
            --status-disconnected: var(--red-accent);
        }
        ::selection { background-color: var(--purple-accent); color: white; }
        body {
            font-family: var(--font-family);
            background-color: var(--bg-color);
            color: var(--text-color);
            margin: 0;
            padding: 0;
            overflow: hidden;
        }
        
        /* --- Top Navbar --- */
        nav {
            height: 50px;
            background: #1e1e1e;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.2);
            padding: 0 25px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid var(--border-color);
            z-index: 1000;
        }
        .nav-title {
            font-size: 1.5em;
            font-weight: 700;
            color: var(--accent-color);
        }
        .nav-status {
            display: flex;
            align-items: center;
            gap: 25px;
        }
        .status-light {
            font-weight: 600;
            color: var(--status-disconnected);
            transition: color 0.3s ease;
        }
        .status-light.connected { color: var(--status-connected); }
        .status-light::before {
            content: '●';
            margin-right: 8px;
            font-size: 1.2em;
        }
        .port-name {
            font-size: 0.9em;
            color: var(--text-secondary);
        }
        
        .nav-item {
            font-family: var(--font-family-mono);
            font-size: 1.0em;
            font-weight: 500;
            color: var(--text-secondary);
            display: flex;
            align-items: center;
        }
        .nav-item .material-icons-outlined {
            font-size: 1.3em;
            vertical-align: -4px;
            margin-right: 6px;
        }
        #vbat-display.low-bat {
            color: var(--red-accent);
            font-weight: 700;
        }
        #rssi-display.low-rssi {
            color: var(--red-accent);
            font-weight: 700;
        }

        .dashboard-grid {
            display: grid;
            grid-template-columns: 2fr 1fr 1fr 1fr;
            grid-template-rows: 1fr 1fr 2fr;
            height: calc(100vh - 50px); 
            gap: 10px;
            padding: 10px;
            box-sizing: border-box;
        }
        .panel {
            background: var(--panel-bg);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            box-shadow: var(--shadow);
            padding: 15px;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }
        .panel-title {
            font-size: 1.1em;
            font-weight: 600;
            color: var(--text-secondary);
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 1px solid var(--border-color);
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        #p-keydata {
            grid-column: 1 / 2;
            grid-row: 1 / 3;
            display: grid;
            grid-template-columns: 1fr 1fr;
            grid-template-rows: repeat(3, 1fr);
            gap: 15px;
        }
        #p-attitude {
            grid-column: 2 / 4;
            grid-row: 1 / 2;
            flex-direction: row;
            justify-content: space-around;
            align-items: center;
        }
        #p-gps {
            grid-column: 2 / 4;
            grid-row: 2 / 3;
        }
        #p-charts {
            grid-column: 4 / 5;
            grid-row: 1 / 4;
            overflow-y: auto;
        }
        #p-log {
            grid-column: 1 / 4;
            grid-row: 3 / 4;
        }
        
        .readout {
            background: rgba(0,0,0,0.2);
            border-radius: 8px;
            padding: 15px;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
        }
        .readout-label { font-size: 1.2em; font-weight: 500; color: var(--text-secondary); }
        .readout-value {
            font-family: var(--font-family-mono);
            font-size: 3.5em;
            font-weight: 700;
            color: var(--primary-color);
            line-height: 1.2;
        }
        .readout-unit { font-size: 1.2em; color: var(--text-secondary); margin-left: 5px; }
        #readout-max-alt .readout-value { color: var(--accent-color); }
        
        .gauge-container {
            flex: 1;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-width: 0;
        }
        .gauge-container canvas { max-width: 100%; max-height: 150px; }
        .gauge-value {
            font-family: var(--font-family-mono);
            font-size: 2em;
            font-weight: 700;
            color: var(--text-color);
            margin-top: -30px;
            text-shadow: 0 0 5px var(--bg-color);
        }
        .gauge-label {
            font-size: 1.1em;
            font-weight: 500;
            color: var(--text-secondary);
            margin-top: 10px;
        }
        
        .gps-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; height: 100%; }
        .gps-readout { display: flex; flex-direction: column; }
        .gps-label { font-size: 0.9em; color: var(--text-secondary); text-transform: uppercase; }
        .gps-value { font-family: var(--font-family-mono); font-size: 1.5em; font-weight: 500; color: var(--text-color); }
        #data-fix-status { color: var(--red-accent); }
        #data-fix-status.locked { color: var(--green-accent); }

        .chart-container { width: 100%; height: 180px; position: relative; margin-bottom: 10px; }
        .chart-title { font-size: 1em; font-weight: 500; color: var(--text-secondary); text-align: center; margin-bottom: 5px; }

        #log-panel-body {
            flex-grow: 1;
            overflow-y: scroll;
            background-color: var(--bg-color);
            border: 1px solid var(--border-color);
            border-radius: 4px;
            padding: 10px;
            font-family: var(--font-family-mono);
            font-size: 0.9em;
            color: var(--text-color);
            white-space: pre-wrap;
            word-wrap: break-word;
        }
        .log-entry { padding: 2px 0; border-bottom: 1px dashed var(--border-color); }
        .log-time { color: var(--text-secondary); margin-right: 10px; }
        .log-status { font-weight: bold; }
        .log-status.connected { color: var(--status-connected); }
        .log-status.disconnected { color: var(--status-disconnected); }
        
        .material-icons-outlined { font-size: inherit; vertical-align: -3px; }
    </style>
</head>
<body>
    <nav>
        <div class="nav-title">
            <span class="material-icons-outlined" style="font-size: 1.2em; vertical-align: -5px;">satellite_alt</span>
            SlugSight Ground Station
        </div>
        <div class="nav-status">
            <span class="nav-item" id="vbat-display">
                <span class="material-icons-outlined">battery_unknown</span>--% (-- V)
            </span>
            <span class="nav-item" id="rssi-display">
                <span class="material-icons-outlined">signal_cellular_off</span>-- dBm
            </span>
            <span class="status-light" id="arduino-status">Connecting...</span>
            <span class="port-name" id="port-name"></span>
        </div>
    </nav>

    <main class="dashboard-grid">
        <div class="panel" id="p-keydata">
            <div class="readout" id="readout-alt">
                <div class="readout-label">Altitude</div>
                <div class="readout-value"><span id="data-alt">0.0</span><span class="readout-unit">m</span></div>
            </div>
            <div class="readout" id="readout-max-alt">
                <div class="readout-label">Max Altitude</div>
                <div class="readout-value"><span id="data-max-alt">0.0</span><span class="readout-unit">m</span></div>
            </div>
            <div class="readout" id="readout-vel">
                <div class="readout-label">Velocity</div>
                <div class="readout-value"><span id="data-vel">0.0</span><span class="readout-unit">m/s</span></div>
            </div>
            <div class="readout" id="readout-pressure">
                <div class="readout-label">Pressure</div>
                <div class="readout-value"><span id="data-pressure">0.00</span><span class="readout-unit">hPa</span></div>
            </div>
            <div class="readout" id="readout-gforce">
                <div class="readout-label">Max G-Force</div>
                <div class="readout-value"><span id="data-max-g">0.0</span><span class="readout-unit">g</span></div>
            </div>
            <div class="readout" id="readout-temp">
                <div class="readout-label">IMU Temp</div>
                <div class="readout-value"><span id="data-temp">0.0</span><span class="readout-unit">°C</span></div>
            </div>
        </div>
        
        <div class="panel" id="p-attitude">
            <div class="gauge-container">
                <div class="panel-title" style="border: none; margin: 0; justify-content: center;">
                    <span class="material-icons-outlined">navigation</span> Pitch
                </div>
                <canvas id="pitch-gauge"></canvas>
                <div class="gauge-value" id="pitch-value">0°</div>
            </div>
            <div class="gauge-container">
                <div class="panel-title" style="border: none; margin: 0; justify-content: center;">
                    <span class="material-icons-outlined">replay</span> Roll
                </div>
                <canvas id="roll-gauge"></canvas>
                <div class="gauge-value" id="roll-value">0°</div>
            </div>
            <div class="gauge-container">
                <div class="panel-title" style="border: none; margin: 0; justify-content: center;">
                    <span class="material-icons-outlined">explore</span> Heading
                </div>
                <canvas id="heading-gauge"></canvas>
                <div class="gauge-value" id="heading-value">0°</div>
            </div>
        </div>
        
        <div class="panel" id="p-gps">
            <div class="panel-title">
                <span class="material-icons-outlined">public</span> GPS Status
            </div>
            <div class="gps-grid">
                <div class="gps-readout">
                    <div class="gps-label">Fix Status</div>
                    <div class="gps-value" id="data-fix-status">No Fix</div>
                </div>
                <div class="gps-readout">
                    <div class="gps-label">Satellites</div>
                    <div class="gps-value" id="data-sats">0</div>
                </div>
                <div class="gps-readout">
                    <div class="gps-label">Latitude</div>
                    <div class="gps-value" id="data-lat">0.000000</div>
                </div>
                <div class="gps-readout">
                    <div class="gps-label">Longitude</div>
                    <div class="gps-value" id="data-lon">0.000000</div>
                </div>
                <div class="gps-readout">
                    <div class="gps-label">GPS Altitude</div>
                    <div class="gps-value"><span id="data-gps-alt">0.0</span> m</div>
                </div>
                <div class="gps-readout">
                    <div class="gps-label">GPS Speed</div>
                    <div class="gps-value"><span id="data-gps-speed">0.0</span> m/s</div>
                </div>
            </div>
        </div>
        
        <div class="panel" id="p-charts">
            <div class="panel-title">
                <span class="material-icons-outlined">timeline</span> Telemetry Charts
            </div>
        </div>
        
        <div class="panel" id="p-log">
            <div class="panel-title">
                <span class="material-icons-outlined">article</span> Event Log
            </div>
            <div id="log-panel-body"></div>
        </div>
    </main>

    <script>
        // --- Global State ---
        let attitudeGauges = {};
        let lineChartObjects = {};
        let maxAltitude = 0.0;
        let maxGForce = 0.0;
        const MAX_CHART_POINTS = 100;
        
        // --- 18-POINT DATA CONTRACT ---
        const DATA_LABELS = [
            "Pitch", "Roll", "Yaw",
            "Altitude", "Velocity",
            "Accel X", "Accel Y", "Accel Z",
            "Pressure Pa", "IMU Temp C",
            "GPS Fix", "GPS Sats",
            "GPS Lat", "GPS Lon", "GPS Alt m", "GPS Speed m/s",
            "VBat", "RSSI"
        ];
        
        const DISPLAY_CHARTS = [
            { label: "Altitude", unit: "m" },
            { label: "Velocity", unit: "m/s" },
            { label: "Pressure Pa", unit: "Pa" },
            { label: "Accel X", unit: "g" },
            { label: "Accel Y", unit: "g" },
            { label: "Accel Z", unit: "g" },
            { label: "VBat", unit: "V" },
            { label: "RSSI", unit: "dBm" }
        ];

        const chartColors = {
            "Altitude": "var(--primary-color)",
            "Velocity": "var(--green-accent)",
            "Pressure Pa": "var(--purple-accent)",
            "Accel X": "var(--red-accent)",
            "Accel Y": "var(--accent-color)",
            "Accel Z": "var(--primary-color)",
            "VBat": "var(--accent-color)",
            "RSSI": "var(--green-accent)",
        };

        const logPanelBody = document.getElementById('log-panel-body');
        function logToPanel(message, type = 'data') {
            const time = new Date().toLocaleTimeString();
            const entry = document.createElement('div');
            entry.classList.add('log-entry');
            let statusClass = '';
            if (type === 'connect') statusClass = 'log-status connected';
            if (type === 'disconnect') statusClass = 'log-status disconnected';
            entry.innerHTML = `<span class="log-time">[${time}]</span> <span class="${statusClass}">${message}</span>`;
            logPanelBody.appendChild(entry);
            logPanelBody.scrollTop = logPanelBody.scrollHeight;
        }

        function voltageToPercentage(v) {
            // Simple linear mapping for a 3.7V LiPo
            // 4.2V = 100%, 3.2V = 0%
            const minV = 3.2;
            const maxV = 4.2;
            let percent = (v - minV) / (maxV - minV);
            percent = Math.max(0, Math.min(1, percent)); // Clamp 0-1
            return Math.round(percent * 100);
        }

        const gaugeConfig = (min, max, circumference = 180, rotation = -90) => ({
            type: 'doughnut',
            data: { datasets: [{
                data: [0, max - min],
                backgroundColor: [ 'var(--primary-color)', 'var(--border-color)' ],
                borderColor: 'rgba(0,0,0,0)',
                borderWidth: 0,
                circumference: circumference,
                rotation: rotation,
            }]},
            options: {
                responsive: true,
                maintainAspectRatio: true,
                cutout: '70%',
                plugins: { legend: { display: false }, tooltip: { enabled: false } },
                animation: { duration: 0 }
            }
        });
        
        function updateGauge(chart, value, min, max, elementId, unit) {
            if (!chart) return;
            const val = parseFloat(value);
            const clampedVal = Math.max(min, Math.min(val, max));
            const dataValue = clampedVal - min;
            const range = max - min;
            
            chart.data.datasets[0].data[0] = dataValue;
            chart.data.datasets[0].data[1] = range - dataValue;
            
            const ctx = chart.ctx;
            const chartArea = chart.chartArea;
            if (chartArea) {
                const gradient = ctx.createLinearGradient(0, chartArea.bottom, 0, chartArea.top);
                gradient.addColorStop(0, "var(--primary-color)");
                gradient.addColorStop(1, "var(--purple-accent)");
                chart.data.datasets[0].backgroundColor[0] = gradient;
            }
            chart.data.datasets[0].backgroundColor[1] = "var(--border-color)";
            chart.update('none'); 
            document.getElementById(elementId).innerHTML = `${val.toFixed(0)}${unit}`;
        }
        
        const lineChartConfig = (label, color) => ({
            type: 'line',
            data: { datasets: [{
                label: label,
                data: [],
                borderColor: color,
                backgroundColor: color.replace(')', ', 0.2)').replace('rgb', 'rgba'),
                borderWidth: 2,
                pointRadius: 0,
                tension: 0.1
            }] },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: { type: 'timeseries', time: { unit: 'second' }, ticks: { display: false, color: 'var(--text-secondary)' }, grid: { color: 'rgba(255, 255, 255, 0.1)' } },
                    y: { ticks: { color: 'var(--text-secondary)' }, grid: { color: 'rgba(255, 255, 255, 0.1)' } }
                },
                plugins: { legend: { display: false } },
                animation: { duration: 0 }
            }
        });
        
        function updateLineChart(chart, timestamp, value) {
            if (!chart) return;
            chart.data.datasets[0].data.push({ x: timestamp, y: value });
            while (chart.data.datasets[0].data.length > MAX_CHART_POINTS) {
                chart.data.datasets[0].data.shift();
            }
            chart.update('none');
        }
        
        function initializeGauges() {
            attitudeGauges.pitch = new Chart(document.getElementById('pitch-gauge').getContext('2d'), gaugeConfig(-90, 90, 180, -90));
            attitudeGauges.roll = new Chart(document.getElementById('roll-gauge').getContext('2d'), gaugeConfig(-180, 180, 360, 0));
            attitudeGauges.heading = new Chart(document.getElementById('heading-gauge').getContext('2d'), gaugeConfig(0, 360, 360, 0));
        }
        
        function initializeLineCharts() {
            const chartPanel = document.getElementById('p-charts');
            DISPLAY_CHARTS.forEach(chartDef => {
                const label = chartDef.label;
                const container = document.createElement('div');
                container.className = 'chart-container';
                const title = document.createElement('div');
                title.className = 'chart-title';
                title.textContent = `${label} (${chartDef.unit})`;
                const canvas = document.createElement('canvas');
                canvas.id = `chart-${label}`;
                container.appendChild(title);
                container.appendChild(canvas);
                chartPanel.appendChild(container);
                const color = chartColors[label] || 'var(--primary-color)';
                const ctx = canvas.getContext('2d');
                lineChartObjects[label] = new Chart(ctx, lineChartConfig(label, color));
            });
        }

        let lastConnectionState = false;
        function updateStatus(status) {
            const statusEl = document.getElementById('arduino-status');
            const portEl = document.getElementById('port-name');
            
            if (status.arduino_connected) {
                statusEl.textContent = 'Connected';
                statusEl.classList.add('connected');
                portEl.textContent = `on ${status.port}`;
                if (!lastConnectionState) {
                    logToPanel(`GCS Receiver connection established on ${status.port}`, 'connect');
                    lastConnectionState = true;
                }
            } else {
                statusEl.textContent = 'Disconnected';
                statusEl.classList.remove('connected');
                if (status.port === "WebSocket Closed") {
                    portEl.textContent = '(WebSocket Closed)';
                } else {
                    portEl.textContent = `(${status.port})`;
                }
                if (lastConnectionState) {
                    logToPanel('GCS Receiver connection lost.', 'disconnect');
                    lastConnectionState = false;
                }
            }
        }

        function updateGUI(data, timestamp) {
            try {
                // --- 1. Update Top Bar (VBAT & RSSI) ---
                const vbat = parseFloat(data['VBat']);
                const rssi = parseFloat(data['RSSI']);
                const vbatEl = document.getElementById('vbat-display');
                const rssiEl = document.getElementById('rssi-display');

                const percent = voltageToPercentage(vbat);
                let batIcon = 'battery_full';
                vbatEl.classList.remove('low-bat');

                if (percent < 10) {
                    batIcon = 'battery_alert';
                    vbatEl.classList.add('low-bat');
                } else if (percent < 25) {
                    batIcon = 'battery_1_bar';
                } else if (percent < 50) {
                    batIcon = 'battery_3_bar';
                } else if (percent < 75) {
                    batIcon = 'battery_5_bar';
                }
                vbatEl.innerHTML = `<span class="material-icons-outlined">${batIcon}</span> ${percent}% (${vbat.toFixed(2)} V)`;


                // Update RSSI
                let rssiIcon = 'signal_cellular_4_bar';
                rssiEl.classList.remove('low-rssi');
                if (rssi < -100) {
                    rssiIcon = 'signal_cellular_0_bar';
                    rssiEl.classList.add('low-rssi');
                } else if (rssi < -90) {
                    rssiIcon = 'signal_cellular_1_bar';
                } else if (rssi < -80) {
                    rssiIcon = 'signal_cellular_2_bar';
                } else if (rssi < -70) {
                    rssiIcon = 'signal_cellular_3_bar';
                }
                rssiEl.innerHTML = `<span class="material-icons-outlined">${rssiIcon}</span> ${rssi.toFixed(0)} dBm`;

                // --- 2. Update Key Readouts ---
                const altitude = parseFloat(data['Altitude']);
                if (altitude > maxAltitude) {
                    maxAltitude = altitude;
                    document.getElementById('data-max-alt').textContent = maxAltitude.toFixed(1);
                }
                document.getElementById('data-alt').textContent = altitude.toFixed(1);
                document.getElementById('data-vel').textContent = parseFloat(data['Velocity']).toFixed(1);
                document.getElementById('data-pressure').textContent = (parseFloat(data['Pressure Pa']) / 100).toFixed(2); // Pa to hPa
                document.getElementById('data-temp').textContent = parseFloat(data['IMU Temp C']).toFixed(1);
                
                const ax = parseFloat(data['Accel X']);
                const ay = parseFloat(data['Accel Y']);
                const az = parseFloat(data['Accel Z']);
                const gForce = Math.sqrt(ax*ax + ay*ay + az*az);
                if (gForce > maxGForce) {
                    maxGForce = gForce;
                    document.getElementById('data-max-g').textContent = maxGForce.toFixed(1);
                }

                // --- 3. Update Attitude Gauges ---
                updateGauge(attitudeGauges.pitch, data['Pitch'], -90, 90, 'pitch-value', '°');
                updateGauge(attitudeGauges.roll, data['Roll'], -180, 180, 'roll-value', '°');
                let yaw = parseFloat(data['Yaw']);
                if (yaw < 0) yaw += 360; // Ensure 0-360 range
                updateGauge(attitudeGauges.heading, yaw, 0, 360, 'heading-value', '°');

                // --- 4. Update GPS Panel ---
                const fix = parseInt(data['GPS Fix'], 10);
                const fixEl = document.getElementById('data-fix-status');
                if (fix > 0) {
                    fixEl.textContent = '3D Fix Locked';
                    fixEl.classList.add('locked');
                } else {
                    fixEl.textContent = 'No Fix';
                    fixEl.classList.remove('locked');
                }
                document.getElementById('data-sats').textContent = data['GPS Sats'];
                document.getElementById('data-lat').textContent = parseFloat(data['GPS Lat']).toFixed(6);
                document.getElementById('data-lon').textContent = parseFloat(data['GPS Lon']).toFixed(6);
                document.getElementById('data-gps-alt').textContent = parseFloat(data['GPS Alt m']).toFixed(1);
                document.getElementById('data-gps-speed').textContent = parseFloat(data['GPS Speed m/s']).toFixed(1);
                
                // --- 5. Update Line Charts ---
                DISPLAY_CHARTS.forEach(chartDef => {
                    const label = chartDef.label;
                    if (data[label] !== undefined) {
                        updateLineChart(lineChartObjects[label], timestamp, parseFloat(data[label]));
                    }
                });

            } catch (error) {
                console.error("Error updating GUI:", error);
                logToPanel(`Error updating GUI: ${error.message}`, 'disconnect');
            }
        }
        
        function connectWebSocket() {
            const wsProtocol = window.location.protocol === "https" ? "wss" : "ws";
            const wsURL = `${wsProtocol}://${window.location.host}/ws`;
            
            logToPanel(`Connecting to WebSocket at ${wsURL}...`);
            const ws = new WebSocket(wsURL);

            ws.onopen = () => { logToPanel("WebSocket connection established.", 'connect'); };

            ws.onmessage = (event) => {
                try {
                    const message = JSON.parse(event.data);
                    if (message.type === 'update') {
                        const timestamp = new Date();
                        updateStatus(message.status);
                        updateGUI(message.data, timestamp);
                    }
                } catch (e) {
                    logToPanel(`Error parsing packet: ${e.message}`, 'disconnect');
                }
            };

            ws.onclose = () => {
                logToPanel("WebSocket connection closed. Reconnecting in 3s...", 'disconnect');
                updateStatus({ arduino_connected: false, port: "WebSocket Closed" });
                // Reset nav bar items to unknown
                document.getElementById('vbat-display').innerHTML = '<span class="material-icons-outlined">battery_unknown</span>--% (-- V)';
                document.getElementById('rssi-display').innerHTML = '<span class="material-icons-outlined">signal_cellular_off</span>-- dBm';
                setTimeout(connectWebSocket, 3000); 
            };

            ws.onerror = (error) => {
                logToPanel(`WebSocket error. Closing connection.`, 'disconnect');
                ws.close();
            };
        }

        document.addEventListener('DOMContentLoaded', () => {
            initializeGauges();
            initializeLineCharts();
            connectWebSocket();
            logToPanel('Dashboard initialized. Waiting for data...');
        });
    </script>
</body>
</html>
"""

# --- Part 3: Main Execution (Python) ---
app = Flask(__name__)
sock = Sock(app)

@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE)

@sock.route('/ws')
def ws(ws):
    global global_clients
    logger.info("WebSocket client connected.")
    with data_lock:
        global_clients.append(ws)
    
    try:
        with data_lock:
            payload = json.dumps({
                "type": "update",
                "status": global_status,
                "data": global_data
            })
        ws.send(payload)
    except Exception as e:
        logger.error(f"Error on initial send to new client: {e}")

    try:
        while True:
            message = ws.receive(timeout=60)
    except Exception as e:
        logger.info(f"WebSocket client disconnected.")
    finally:
        with data_lock:
            if ws in global_clients:
                global_clients.remove(ws)
        logger.info("Client removed from list.")

if __name__ == "__main__":
    # 1. Find the GCS Receiver Arduino
    arduino_port = find_arduino_port()
    if not arduino_port:
        logger.error("Could not find a GCS Receiver Arduino.")
        logger.error("Please check connection and ARDUINO_VID_PIDS list.")
        with data_lock:
            global_status["port"] = "Not Found"
    else:
        with data_lock:
            global_status["port"] = arduino_port
        
        # 2. Start the serial reader thread
        reader = threading.Thread(target=serial_reader_thread, args=(arduino_port,), daemon=True)
        reader.start()

    # 3. Start the Flask web server
    logger.info("\n--- SlugSight Ground Station Server ---")
    logger.info(f"Data logging to: {data_logger.get_current_file()}")
    logger.info(f"Open this URL in your browser: http://127.0.0.1:5000")
    logger.info("----------------------------------------")
    
    try:
        app.run(host='0.0.0.0', port=5000, debug=False)
    except KeyboardInterrupt:
        logger.info("\nShutting down...")
    finally:
        # Close data logger on exit
        data_logger.close()
        logger.info("Ground station shutdown complete.")