
import time
import json
import threading
import concurrent.futures
from flask import Flask, request, jsonify, render_template_string
import serial
from serial.tools import list_ports

BAUD = 115200
TIMEOUT = 2.0
READ_TIMEOUT_S = 10.0

HTML = """<!DOCTYPE html>
<html>
<head>
  <meta charset='utf-8' />
  <title>ESP32 Cluster Dashboard</title>
  <style>
    :root { --bg:#0b1020; --card:#131a33; --ink:#e7ecff; --muted:#9fb2ff; --accent:#6aa7ff; }
    * { box-sizing: border-box; }
    body { margin:0; font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
           background: radial-gradient(1200px 700px at 10% -10%, #1d2547 0%, #0b1020 45%);
           color: var(--ink); }
    header { padding: 20px; display:flex; align-items:center; justify-content:space-between; }
    h1 { margin:0; font-size: 22px; letter-spacing: 0.3px; }
    .bar { display:flex; gap:8px; flex-wrap:wrap; }
    button, .input {
      background: linear-gradient(180deg,#2a376f,#202955);
      border: 1px solid #31407e; color: var(--ink);
      padding: 10px 14px; border-radius: 12px; cursor:pointer; font-weight:600;
      box-shadow: 0 1px 0 rgba(255,255,255,0.06) inset, 0 6px 20px rgba(0,0,0,0.25);
    }
    button:hover { filter: brightness(1.08); }
    .input { display:inline-flex; align-items:center; gap:8px; }
    .input input { width: 90px; background: transparent; border:none; color:var(--ink); outline:none; font-weight:600; }
    main { padding: 20px; max-width: 1100px; margin: 0 auto; }
    .grid { display:grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap:14px; margin-top: 14px; }
    .card { background: linear-gradient(180deg,#111736,#0f1530);
            border:1px solid #1e2a59; border-radius: 16px; padding: 14px; }
    .title { font-size: 13px; color: var(--muted); margin-bottom:8px; }
    .big { font-size: 20px; font-weight: 700; color: #fff; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    .ok { color: #7cf59b; }
    .err { color: #ff9ea8; }
    .muted { color: var(--muted); }
    .footer { margin-top: 16px; display:flex; align-items:center; gap:14px; flex-wrap:wrap; }
    .pill { background:#0f1b40; border:1px solid #20306d; padding:6px 10px; border-radius: 999px; font-size:12px; color: #c7d5ff; }
    .total { font-size: 24px; font-weight:800; color:#b7cbff; }
    .ports { font-size: 13px; color:#c7d5ff; }
  </style>
</head>
<body>
  <header>
    <h1>ESP32 Cluster Dashboard</h1>
    <div class='bar'>
      <button onclick='refreshPorts()'>Refresh Boards</button>
      <div class='input'> FIB n=<input id='fibN' type='number' value='40' min='0' max='93'></div>
      <button onclick='runFib()'>Run Fibonacci</button>
      <div class='input'> Hashes=<input id='hashN' type='number' value='5000' min='1'></div>
      <button onclick='runHash()'>Run Hash</button>
      <button onclick='sendCmd("BLINK")'>Blink</button>
      <div class='input'> Cycles=<input id='lsCycles' type='number' value='5' min='1'>
        Delay(ms)=<input id='lsDelay' type='number' value='100' min='1'></div>
      <button onclick='runLightshow()'>Lightshow</button>
      <button onclick='sendCmd("STATUS")'>Status</button>
    </div>
  </header>

  <main>
    <div class='card'>
      <div class='title'>Connected Boards</div>
      <div id='ports' class='ports mono'>—</div>
      <div class='footer'>
        <span class='pill' id='count'>0 boards</span>
        <span class='pill' id='last-run'>Ready</span>
        <span class='pill'>Server running on <span class='mono'>http://localhost:5000</span></span>
      </div>
    </div>

    <div class='grid' id='results'></div>

    <div class='card' id='totalBox' style='display:none;'>
      <div class='title'>Total Hashrate</div>
      <div id='total' class='total mono'>0 H/s</div>
    </div>
  </main>

  <script>
    function fmtRate(hs) {
      if (hs >= 1e9) return (hs/1e9).toFixed(2) + " GH/s";
      if (hs >= 1e6) return (hs/1e6).toFixed(2) + " MH/s";
      if (hs >= 1e3) return (hs/1e3).toFixed(2) + " kH/s";
      return Number(hs).toFixed(2) + " H/s";
    }

    function card(port, lines) {
      const ok = lines.some(l => l.startsWith('RESULT') || l.startsWith('DONE') || l.startsWith('HASH_DONE') || l.startsWith('PONG'));
      const cls = ok ? 'ok' : 'err';
      let html = `<div class='card'><div class='title'>${port}</div>`;
      if (lines.length === 0) {
        html += `<div class='muted'>No data</div>`
      } else {
        lines.forEach(l => {
          if (l.startsWith('HASH_DONE')) {
            const v = parseFloat(l.split(' ')[1]||'0');
            html += `<div class='big ${cls}'>Hash: ${fmtRate(v)}</div>`;
          } else {
            html += `<div class='mono ${cls}'>${l}</div>`;
          }
        })
      }
      html += '</div>';
      return html;
    }

    function updateResults(data) {
      const grid = document.getElementById('results');
      grid.innerHTML = data.results.map(r => card(r[0], r[1])).join('');
      document.getElementById('ports').innerText = data.ports.join(', ');
      document.getElementById('count').innerText = data.ports.length + ' boards';
      if (data.total_hs && data.total_hs > 0) {
        document.getElementById('totalBox').style.display = 'block';
        document.getElementById('total').innerText = fmtRate(data.total_hs);
      } else {
        document.getElementById('totalBox').style.display = 'none';
      }
      document.getElementById('last-run').innerText = 'Last run: ' + new Date().toLocaleTimeString();
    }

    async function refreshPorts() {
      const r = await fetch('/ports');
      const data = await r.json();
      updateResults({results: data.ports.map(p => [p, []]), ports: data.ports, total_hs: 0});
    }

    async function sendCmd(cmd) {
      const r = await fetch('/command', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cmd })
      });
      const data = await r.json();
      updateResults(data);
    }

    function runFib() {
      const n = document.getElementById('fibN').value || '40';
      sendCmd('FIB ' + n);
    }
    function runHash() {
      const n = document.getElementById('hashN').value || '5000';
      sendCmd('HASH ' + n);
    }
    function runLightshow() {
      const c = document.getElementById('lsCycles').value || '5';
      const d = document.getElementById('lsDelay').value || '100';
      sendCmd(`LIGHTSHOW ${c} ${d}`);
    }

    // init
    refreshPorts();
  </script>
</body>
</html>"""

app = Flask(__name__)

# ---------------- ESP32 helpers ----------------
def find_esp32_ports():
    ports = []
    for p in list_ports.comports():
        desc = (p.description or '').lower()
        if any(k in desc for k in ['usb', 'esp32', 'cp210', 'ch340', 'wch']):
            ports.append(p.device)
    return ports

def _read_lines(ser, stop_after_first_done=True):
    lines = []
    start = time.time()
    while True:
        try:
            line = ser.readline().decode(errors='ignore').strip()
        except Exception:
            break
        if line:
            lines.append(line)
            if stop_after_first_done and (
                line.startswith('DONE') or
                line.startswith('RESULT') or
                line.startswith('HASH_DONE') or
                line.startswith('ERR') or
                line.startswith('PONG')
            ):
                break
        if time.time() - start > READ_TIMEOUT_S:
            break
    return lines

def send_command(port, cmd):
    try:
        ser = serial.Serial(port, BAUD, timeout=TIMEOUT)
        time.sleep(0.2)
        ser.reset_input_buffer()
        ser.write((cmd + '\n').encode())
        lines = _read_lines(ser, stop_after_first_done=True)
        ser.close()
        return port, lines
    except Exception as e:
        return port, [f'ERR {e}']

def run_all(cmd):
    ports = find_esp32_ports()
    results = []
    if not ports:
        return [], [('NONE', ['No ESP32 boards found!'])], 0.0
    total_hs = 0.0
    with concurrent.futures.ThreadPoolExecutor() as ex:
        for port, lines in ex.map(lambda p: send_command(p, cmd), ports):
            results.append((port, lines))
            for line in lines:
                if line.startswith('HASH_DONE'):
                    try:
                        total_hs += float(line.split()[1])
                    except:
                        pass
    return ports, results, total_hs

# ---------------- Routes ----------------
@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/ports')
def ports():
    ps = find_esp32_ports()
    return jsonify({'ports': ps})

@app.route('/command', methods=['POST'])
def command():
    data = request.get_json(force=True)
    cmd = data.get('cmd', '').strip()
    ports, results, total_hs = run_all(cmd)
    return jsonify({'ports': ports, 'results': results, 'total_hs': total_hs})

if __name__ == '__main__':
    print('Starting ESP32 Cluster Dashboard on http://localhost:5000')
    print('Tip: close Arduino Serial Monitor before running this.')
    app.run(host='0.0.0.0', port=5000, debug=True)
