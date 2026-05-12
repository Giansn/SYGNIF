/* NeuroLinked Brain Dashboard - Dark Theme + Glassmorphism */

* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

@font-face {
    font-family: 'JetBrains';
    src: local('JetBrains Mono'), local('Consolas'), local('monospace');
}

body {
    background: #020408;
    color: #e0e0e0;
    font-family: 'JetBrains', 'Consolas', 'SF Mono', monospace;
    overflow: hidden;
    width: 100vw;
    height: 100vh;
    cursor: crosshair;
}

#canvas-container {
    position: fixed;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    z-index: 0;
}

canvas {
    display: block;
}

/* --- Glass panels --- */
.glass-panel {
    position: fixed;
    background: rgba(8, 12, 20, 0.75);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 12px;
    padding: 16px;
    z-index: 10;
    box-shadow:
        0 8px 32px rgba(0, 0, 0, 0.6),
        inset 0 1px 0 rgba(255, 255, 255, 0.05);
    transition: opacity 0.3s ease;
}

/* --- Top-left: Neural Activity Stats --- */
#stats-panel {
    top: 20px;
    left: 20px;
    width: 320px;
}

.stats-title {
    color: #00e5ff;
    font-size: 11px;
    letter-spacing: 3px;
    text-transform: uppercase;
    margin-bottom: 12px;
    text-shadow: 0 0 10px rgba(0, 229, 255, 0.4);
}

.stat-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 6px;
    font-size: 12px;
}

.stat-label {
    color: #888;
    font-size: 11px;
}

.stat-value {
    color: #fff;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
}

.stat-value.live {
    color: #00e676;
}

/* --- Development stage badge --- */
.stage-badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 4px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 2px;
    margin-top: 8px;
}

.stage-EMBRYONIC { background: #1a237e; color: #7c8aff; }
.stage-JUVENILE { background: #1b5e20; color: #69f0ae; }
.stage-ADOLESCENT { background: #e65100; color: #ffab40; }
.stage-MATURE { background: #880e4f; color: #ff80ab; }

/* --- Live indicator --- */
.live-indicator {
    display: flex;
    align-items: center;
    gap: 6px;
    float: right;
    font-size: 10px;
    color: #00e676;
    letter-spacing: 1px;
}

.live-dot {
    width: 8px;
    height: 8px;
    background: #00e676;
    border-radius: 50%;
    animation: pulse 1.5s ease-in-out infinite;
    box-shadow: 0 0 8px rgba(0, 230, 118, 0.6);
}

@keyframes pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.5; transform: scale(0.8); }
}

/* --- Neurotransmitter bars --- */
#neuro-bars {
    margin-top: 12px;
    padding-top: 10px;
    border-top: 1px solid rgba(255, 255, 255, 0.06);
}

.neuro-bar-row {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 5px;
}

.neuro-bar-label {
    width: 30px;
    font-size: 9px;
    color: #aaa;
    text-align: right;
    letter-spacing: 1px;
}

.neuro-bar-track {
    flex: 1;
    height: 4px;
    background: rgba(255, 255, 255, 0.05);
    border-radius: 2px;
    overflow: hidden;
}

.neuro-bar-fill {
    height: 100%;
    border-radius: 2px;
    transition: width 0.3s ease;
}

.neuro-bar-fill.da { background: linear-gradient(90deg, #ff1744, #ff5252); }
.neuro-bar-fill.ach { background: linear-gradient(90deg, #2979ff, #448aff); }
.neuro-bar-fill.ne { background: linear-gradient(90deg, #00e676, #69f0ae); }
.neuro-bar-fill.sht { background: linear-gradient(90deg, #ff9100, #ffab40); }

/* --- Right side: Region activity --- */
#regions-panel {
    top: 20px;
    right: 20px;
    width: 280px;
    max-height: calc(100vh - 40px);
    overflow-y: auto;
}

.region-row {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
    padding: 4px 8px;
    border-radius: 6px;
    cursor: pointer;
    transition: background 0.2s;
}

.region-row:hover {
    background: rgba(255, 255, 255, 0.05);
}

.region-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    flex-shrink: 0;
}

.region-name {
    flex: 1;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: #ccc;
}

.region-pct {
    font-size: 11px;
    font-weight: 600;
    color: #fff;
    min-width: 40px;
    text-align: right;
    font-variant-numeric: tabular-nums;
}

.region-bar-track {
    width: 60px;
    height: 3px;
    background: rgba(255, 255, 255, 0.05);
    border-radius: 2px;
    overflow: hidden;
    flex-shrink: 0;
}

.region-bar-fill {
    height: 100%;
    border-radius: 2px;
    transition: width 0.3s ease;
}

/* --- Region tooltip / info panel --- */
#region-info {
    bottom: 100px;
    right: 20px;
    width: 320px;
    display: none;
}

#region-info.visible {
    display: block;
}

.info-title {
    font-size: 13px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 2px;
    margin-bottom: 8px;
}

.info-body {
    font-size: 11px;
    line-height: 1.6;
    color: #aaa;
}

/* --- Bottom: Input bar --- */
#input-panel {
    bottom: 20px;
    left: 50%;
    transform: translateX(-50%);
    width: min(600px, calc(100vw - 40px));
    display: flex;
    gap: 10px;
    align-items: center;
    padding: 10px 16px;
}

#text-input {
    flex: 1;
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 8px;
    padding: 10px 14px;
    color: #fff;
    font-family: inherit;
    font-size: 13px;
    outline: none;
    transition: border-color 0.2s;
}

#text-input:focus {
    border-color: rgba(0, 229, 255, 0.4);
    box-shadow: 0 0 12px rgba(0, 229, 255, 0.1);
}

#text-input::placeholder {
    color: #555;
}

.control-btn {
    width: 38px;
    height: 38px;
    border-radius: 8px;
    border: 1px solid rgba(255, 255, 255, 0.1);
    background: rgba(255, 255, 255, 0.05);
    color: #888;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.2s;
    font-size: 16px;
}

.control-btn:hover {
    background: rgba(255, 255, 255, 0.1);
    color: #fff;
}

.control-btn.active {
    background: rgba(0, 229, 255, 0.15);
    border-color: rgba(0, 229, 255, 0.3);
    color: #00e5ff;
}

/* --- Safety panel --- */
#safety-panel {
    bottom: 80px;
    left: 20px;
    width: 200px;
    font-size: 10px;
}

/* --- Claude Integration panel --- */
#claude-panel {
    bottom: 200px;
    left: 20px;
    width: 240px;
    font-size: 10px;
}

#claude-panel .stats-title {
    color: #e040fb;
    text-shadow: 0 0 10px rgba(224, 64, 251, 0.4);
}

#claude-dot {
    background: #e040fb;
    box-shadow: 0 0 8px rgba(224, 64, 251, 0.6);
}

#claude-panel .live-indicator {
    color: #e040fb;
}

.safety-status {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 4px;
}

.safety-icon {
    font-size: 14px;
}

.safety-ok { color: #00e676; }
.safety-warn { color: #ff9100; }
.safety-block { color: #ff1744; }

/* --- Scrollbar styling --- */
::-webkit-scrollbar {
    width: 4px;
}

::-webkit-scrollbar-track {
    background: transparent;
}

::-webkit-scrollbar-thumb {
    background: rgba(255, 255, 255, 0.1);
    border-radius: 2px;
}

/* --- Loading screen --- */
#loading {
    position: fixed;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    background: #000;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    z-index: 1000;
    transition: opacity 0.8s ease;
}

#loading.hidden {
    opacity: 0;
    pointer-events: none;
}

.loading-title {
    font-size: 24px;
    letter-spacing: 8px;
    text-transform: uppercase;
    color: #00e5ff;
    margin-bottom: 20px;
    text-shadow: 0 0 30px rgba(0, 229, 255, 0.3);
}

.loading-bar {
    width: 200px;
    height: 2px;
    background: rgba(255, 255, 255, 0.05);
    border-radius: 1px;
    overflow: hidden;
}

.loading-fill {
    width: 0%;
    height: 100%;
    background: linear-gradient(90deg, #00e5ff, #00e676);
    transition: width 0.3s ease;
}

.loading-status {
    margin-top: 12px;
    font-size: 10px;
    color: #555;
    letter-spacing: 2px;
}

/* --- Floating 3D Region Labels --- */
.region-label-3d {
    font-family: 'JetBrains', 'Consolas', monospace;
    text-shadow: 0 0 8px rgba(0, 0, 0, 0.8);
    transition: opacity 0.3s ease;
}

.region-label-3d:hover {
    background: rgba(0, 0, 0, 0.75) !important;
    border-color: rgba(255, 255, 255, 0.15) !important;
}

.rl-name {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 2px;
    text-transform: uppercase;
    text-shadow: 0 0 12px currentColor;
}

.rl-stats {
    font-size: 9px;
    color: #999;
    letter-spacing: 0.5px;
    margin-top: 1px;
}

.rl-firing {
    color: #aaa;
}

/* --- Graph View button --- */
#graph-view-btn {
    transition: all 0.2s;
}

#graph-view-btn:hover {
    background: rgba(0, 229, 255, 0.1) !important;
    border-color: rgba(0, 229, 255, 0.2) !important;
}
