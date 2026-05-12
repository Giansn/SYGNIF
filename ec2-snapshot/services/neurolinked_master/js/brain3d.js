/**
 * Brain3D - Three.js 3D visualization of the neuromorphic brain.
 * Round orb neurons, floating labels, bloom glow, signal particles for live thinking.
 */
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';

const REGION_COLORS = {
    sensory_cortex: 0x00ffff,
    feature_layer:  0x00ccff,
    association:    0xff6600,
    concept_layer:  0xffcc00,
    predictive:     0xff00ff,
    motor_cortex:   0xff3366,
    cerebellum:     0x66ff66,
    reflex_arc:     0xff4444,
    brainstem:      0xff8800,
    hippocampus:    0x00ff88,
    prefrontal:     0xaa88ff,
};

// Brain topology — regions close together forming one brain, matching original
const REGION_LAYOUT = {
    prefrontal:     { x: -0.6, y:  1.0, z:  0.0 },
    motor_cortex:   { x:  0.5, y:  0.9, z:  0.2 },
    sensory_cortex: { x:  1.0, y:  0.2, z:  0.4 },
    feature_layer:  { x:  0.3, y: -0.3, z:  0.2 },
    association:    { x: -0.1, y:  0.3, z:  0.0 },
    concept_layer:  { x: -0.5, y:  0.6, z: -0.2 },
    predictive:     { x: -0.9, y: -0.1, z: -0.2 },
    hippocampus:    { x: -0.5, y: -0.4, z:  0.2 },
    cerebellum:     { x:  0.1, y: -0.8, z: -0.2 },
    brainstem:      { x:  0.0, y: -0.5, z:  0.0 },
    reflex_arc:     { x:  0.5, y: -0.6, z:  0.3 },
};

// Neural pathway connections for signal particles
const CONNECTIONS = [
    ['sensory_cortex', 'feature_layer'],
    ['feature_layer', 'association'],
    ['association', 'concept_layer'],
    ['concept_layer', 'prefrontal'],
    ['prefrontal', 'motor_cortex'],
    ['motor_cortex', 'cerebellum'],
    ['brainstem', 'reflex_arc'],
    ['brainstem', 'cerebellum'],
    ['hippocampus', 'association'],
    ['hippocampus', 'prefrontal'],
    ['predictive', 'association'],
    ['predictive', 'concept_layer'],
    ['sensory_cortex', 'reflex_arc'],
    ['association', 'hippocampus'],
    ['prefrontal', 'predictive'],
    ['feature_layer', 'concept_layer'],
];

function createNeuronTexture() {
    const size = 64;
    const canvas = document.createElement('canvas');
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext('2d');
    const half = size / 2;
    const gradient = ctx.createRadialGradient(half, half, 0, half, half, half);
    gradient.addColorStop(0, 'rgba(255, 255, 255, 1.0)');
    gradient.addColorStop(0.15, 'rgba(255, 255, 255, 0.8)');
    gradient.addColorStop(0.4, 'rgba(255, 255, 255, 0.3)');
    gradient.addColorStop(0.7, 'rgba(255, 255, 255, 0.05)');
    gradient.addColorStop(1, 'rgba(255, 255, 255, 0.0)');
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, size, size);
    const texture = new THREE.CanvasTexture(canvas);
    texture.needsUpdate = true;
    return texture;
}

const REGION_DESCRIPTIONS = {
    sensory_cortex: 'Primary sensory processing',
    feature_layer:  'Feature extraction & pattern detection',
    association:    'Cross-modal association',
    concept_layer:  'Abstract concept formation',
    predictive:     'Predictive modeling & anticipation',
    motor_cortex:   'Motor output planning',
    cerebellum:     'Timing & coordination',
    reflex_arc:     'Fast reflexive responses',
    brainstem:      'Autonomic regulation',
    hippocampus:    'Memory formation & consolidation',
    prefrontal:     'Executive function & planning',
};

export class Brain3D {
    constructor(container) {
        this.container = container;
        this.time = 0;
        this.regionMeshes = {};
        this.regionGlows = {};
        this.regionData = {};
        this.labels = {};
        this.lastState = null;
        this.graphView = false;

        // Signal particles for "thinking" visualization
        this.signals = [];
        this.maxSignals = 150;

        // Scene
        this.scene = new THREE.Scene();
        this.scene.background = new THREE.Color(0x020408);

        // Camera
        this.camera = new THREE.PerspectiveCamera(
            55, window.innerWidth / window.innerHeight, 0.01, 100
        );
        this.camera.position.set(0, 0.3, 3.5);

        // Renderer
        this.renderer = new THREE.WebGLRenderer({
            antialias: true, alpha: true, powerPreference: 'high-performance'
        });
        this.renderer.setSize(window.innerWidth, window.innerHeight);
        this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
        this.renderer.toneMappingExposure = 1.1;
        container.appendChild(this.renderer.domElement);

        // Bloom - balanced: glowing neurons and active regions light up visibly,
        // but the threshold keeps it from blowing out the bottom cluster.
        this.composer = new EffectComposer(this.renderer);
        this.composer.addPass(new RenderPass(this.scene, this.camera));
        this.bloomPass = new UnrealBloomPass(
            new THREE.Vector2(window.innerWidth, window.innerHeight),
            0.48, 0.30, 0.42
        );
        this.composer.addPass(this.bloomPass);

        // Controls
        this.controls = new OrbitControls(this.camera, this.renderer.domElement);
        this.controls.enableDamping = true;
        this.controls.dampingFactor = 0.05;
        this.controls.rotateSpeed = 0.5;
        this.controls.autoRotate = true;
        this.controls.autoRotateSpeed = 0.2;
        this.controls.target.set(0, 0.1, 0);
        this.controls.minDistance = 2.0;
        this.controls.maxDistance = 15;

        // Lights
        this.scene.add(new THREE.AmbientLight(0x111133, 0.3));
        const centerLight = new THREE.PointLight(0x2244aa, 0.6, 8);
        centerLight.position.set(0, 0.2, 0);
        this.scene.add(centerLight);

        // Label container
        this.labelContainer = document.createElement('div');
        this.labelContainer.id = 'region-labels';
        this.labelContainer.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:5;';
        document.body.appendChild(this.labelContainer);

        // Graph View button
        this._createGraphViewButton();

        // Neuron texture
        this.neuronTexture = createNeuronTexture();

        // Signal particle system
        this._initSignalSystem();

        // Resize
        window.addEventListener('resize', () => this._onResize());

        // Raycaster
        this.raycaster = new THREE.Raycaster();
        this.raycaster.params.Points = { threshold: 0.08 };
        this.mouse = new THREE.Vector2();
        container.addEventListener('click', (e) => this._onClick(e));
    }

    // ===================== SIGNAL PARTICLES (LIVE THINKING) =====================

    _initSignalSystem() {
        const geometry = new THREE.BufferGeometry();
        const positions = new Float32Array(this.maxSignals * 3);
        const colors = new Float32Array(this.maxSignals * 3);
        const sizes = new Float32Array(this.maxSignals);

        geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
        geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
        geometry.setAttribute('size', new THREE.BufferAttribute(sizes, 1));

        const material = new THREE.PointsMaterial({
            size: 0.03,
            map: this.neuronTexture,
            transparent: true,
            opacity: 0.9,
            blending: THREE.AdditiveBlending,
            depthWrite: false,
            sizeAttenuation: true,
            vertexColors: true,
        });

        this.signalMesh = new THREE.Points(geometry, material);
        this.scene.add(this.signalMesh);

        // Pre-allocate signal data
        for (let i = 0; i < this.maxSignals; i++) {
            this.signals.push({
                active: false,
                from: new THREE.Vector3(),
                to: new THREE.Vector3(),
                pos: new THREE.Vector3(),
                progress: 0,
                speed: 0,
                color: new THREE.Color(),
            });
        }
    }

    _spawnSignal(fromRegion, toRegion, color) {
        const from = REGION_LAYOUT[fromRegion];
        const to = REGION_LAYOUT[toRegion];
        if (!from || !to) return;

        // Find an inactive slot
        for (const sig of this.signals) {
            if (!sig.active) {
                sig.active = true;
                sig.from.set(from.x, from.y, from.z);
                sig.to.set(to.x, to.y, to.z);
                sig.pos.copy(sig.from);
                sig.progress = 0;
                sig.speed = 0.4 + Math.random() * 0.8; // variable speed
                sig.color.set(color);

                // Add slight random offset to path
                sig.mid = new THREE.Vector3(
                    (from.x + to.x) / 2 + (Math.random() - 0.5) * 0.3,
                    (from.y + to.y) / 2 + (Math.random() - 0.5) * 0.3,
                    (from.z + to.z) / 2 + (Math.random() - 0.5) * 0.3,
                );
                return;
            }
        }
    }

    _updateSignals(dt) {
        const posArr = this.signalMesh.geometry.attributes.position.array;
        const colArr = this.signalMesh.geometry.attributes.color.array;

        for (let i = 0; i < this.maxSignals; i++) {
            const sig = this.signals[i];
            if (!sig.active) {
                // Hide inactive signals far away
                posArr[i * 3] = 0;
                posArr[i * 3 + 1] = -100;
                posArr[i * 3 + 2] = 0;
                continue;
            }

            sig.progress += dt * sig.speed;

            if (sig.progress >= 1) {
                sig.active = false;
                posArr[i * 3 + 1] = -100;
                continue;
            }

            // Quadratic bezier curve for smooth arc
            const t = sig.progress;
            const t1 = 1 - t;
            sig.pos.x = t1 * t1 * sig.from.x + 2 * t1 * t * sig.mid.x + t * t * sig.to.x;
            sig.pos.y = t1 * t1 * sig.from.y + 2 * t1 * t * sig.mid.y + t * t * sig.to.y;
            sig.pos.z = t1 * t1 * sig.from.z + 2 * t1 * t * sig.mid.z + t * t * sig.to.z;

            posArr[i * 3]     = sig.pos.x;
            posArr[i * 3 + 1] = sig.pos.y;
            posArr[i * 3 + 2] = sig.pos.z;

            colArr[i * 3]     = sig.color.r;
            colArr[i * 3 + 1] = sig.color.g;
            colArr[i * 3 + 2] = sig.color.b;
        }

        this.signalMesh.geometry.attributes.position.needsUpdate = true;
        this.signalMesh.geometry.attributes.color.needsUpdate = true;
    }

    // ===================== NEURON INITIALIZATION =====================

    initNeurons(positions) {
        for (const mesh of Object.values(this.regionMeshes)) this.scene.remove(mesh);
        for (const glow of Object.values(this.regionGlows)) this.scene.remove(glow);
        this.regionMeshes = {};
        this.regionGlows = {};
        this.regionData = positions;
        this.labelContainer.innerHTML = '';
        this.labels = {};

        // Store base positions for jitter animation
        this._basePositions = {};

        for (const [regionName, regionInfo] of Object.entries(positions)) {
            const pts = regionInfo.positions;
            if (!pts || pts.length === 0) continue;

            const layout = REGION_LAYOUT[regionName] || { x: 0, y: 0, z: 0 };
            const center = regionInfo.center || [0, 0, 0];

            const geometry = new THREE.BufferGeometry();
            const posArray = new Float32Array(pts.length * 3);

            // Neurons loosely scattered within each region cluster
            for (let i = 0; i < pts.length; i++) {
                posArray[i * 3]     = (pts[i][0] - center[0]) * 1.2 + layout.x;
                posArray[i * 3 + 1] = (pts[i][1] - center[1]) * 1.2 + layout.y;
                posArray[i * 3 + 2] = (pts[i][2] - center[2]) * 1.2 + layout.z;
            }

            geometry.setAttribute('position', new THREE.BufferAttribute(posArray, 3));

            // Store a copy for jitter reference
            this._basePositions[regionName] = new Float32Array(posArray);

            const color = REGION_COLORS[regionName] || 0xffffff;

            // Main neurons — round orbs (baseline glow boosted so dormant neurons still shimmer)
            const material = new THREE.PointsMaterial({
                color, size: 0.022, map: this.neuronTexture,
                transparent: true, opacity: 1.0,
                blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: true,
            });

            const points = new THREE.Points(geometry, material);
            points.userData = { regionName, center: [layout.x, layout.y, layout.z], count: regionInfo.count };
            this.scene.add(points);
            this.regionMeshes[regionName] = points;

            // Glow layer — wider, stronger so there's always an ambient halo
            const glowGeometry = geometry.clone();
            const glowMaterial = new THREE.PointsMaterial({
                color, size: 0.055, map: this.neuronTexture,
                transparent: true, opacity: 0.16,
                blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: true,
            });
            const glowPoints = new THREE.Points(glowGeometry, glowMaterial);
            this.scene.add(glowPoints);
            this.regionGlows[regionName] = glowPoints;

            this._createLabel(regionName, regionInfo.count);
        }

        console.log(`[Brain3D] Initialized ${Object.keys(this.regionMeshes).length} regions`);
    }

    // ===================== LABELS =====================

    _createLabel(regionName, count) {
        const label = document.createElement('div');
        label.className = 'region-label-3d';
        const colorHex = '#' + (REGION_COLORS[regionName] || 0xffffff).toString(16).padStart(6, '0');
        const displayName = regionName.replace(/_/g, ' ').toUpperCase();
        const countStr = count >= 1000 ? (count / 1000).toFixed(1) + 'K' : count;

        label.innerHTML = `
            <div class="rl-name" style="color: ${colorHex}">${displayName}</div>
            <div class="rl-stats">${countStr} neurons · <span class="rl-firing">firing 0.0%</span></div>
        `;
        label.style.cssText = `
            position: absolute; pointer-events: auto; cursor: pointer;
            padding: 4px 10px; background: rgba(0,0,0,0.55); backdrop-filter: blur(4px);
            border: 1px solid ${colorHex}33; border-radius: 4px;
            font-family: 'JetBrains','Consolas',monospace; white-space: nowrap;
            transition: opacity 0.3s;
        `;

        label.addEventListener('click', () => {
            window.dispatchEvent(new CustomEvent('regionClick', {
                detail: {
                    name: regionName,
                    color: colorHex.replace('#', ''),
                    description: REGION_DESCRIPTIONS[regionName] || `Neural region: ${regionName}`,
                    count: count,
                }
            }));
        });

        this.labelContainer.appendChild(label);
        this.labels[regionName] = label;
    }

    // ===================== GRAPH VIEW =====================

    _createGraphViewButton() {
        const btn = document.createElement('div');
        btn.id = 'graph-view-btn';
        btn.innerHTML = '<span style="font-size:11px;letter-spacing:2px;color:#00e5ff;cursor:pointer;">Graph view</span>';
        btn.style.cssText = `
            position:fixed; bottom:80px; right:20px; padding:8px 16px; z-index:10; cursor:pointer;
            background:rgba(8,12,20,0.75); backdrop-filter:blur(20px);
            border:1px solid rgba(255,255,255,0.06); border-radius:8px;
        `;
        btn.addEventListener('click', () => {
            this.graphView = !this.graphView;
            btn.querySelector('span').textContent = this.graphView ? 'Cloud view' : 'Graph view';
            this._toggleGraphView();
        });
        document.body.appendChild(btn);
    }

    _toggleGraphView() {
        if (this.graphView) {
            this._buildConnectionLines();
        } else if (this._connectionLines) {
            this._connectionLines.forEach(l => this.scene.remove(l));
            this._connectionLines = [];
        }
    }

    _buildConnectionLines() {
        if (this._connectionLines) this._connectionLines.forEach(l => this.scene.remove(l));
        this._connectionLines = [];

        for (const [a, b] of CONNECTIONS) {
            const la = REGION_LAYOUT[a], lb = REGION_LAYOUT[b];
            if (!la || !lb) continue;
            const geo = new THREE.BufferGeometry().setFromPoints([
                new THREE.Vector3(la.x, la.y, la.z),
                new THREE.Vector3(lb.x, lb.y, lb.z),
            ]);
            const mat = new THREE.LineBasicMaterial({
                color: 0x224466, transparent: true, opacity: 0.3, blending: THREE.AdditiveBlending,
            });
            const line = new THREE.Line(geo, mat);
            this.scene.add(line);
            this._connectionLines.push(line);
        }
    }

    // ===================== STATE UPDATE =====================

    updateState(state) {
        if (!state) return;
        this.lastState = state;

        const regionFiring = state.region_firing || {};
        const regions = state.regions || {};

        let totalActivity = 0;

        for (const [name, mesh] of Object.entries(this.regionMeshes)) {
            const firing = regionFiring[name] || 0;
            const regionInfo = regions[name] || {};
            totalActivity += firing;

            // Opacity + size pulse based on firing
            mesh.material.opacity = 0.4 + Math.min(firing * 8, 0.6);
            mesh.material.size = 0.012 + firing * 0.02;

            const glow = this.regionGlows[name];
            if (glow) {
                glow.material.opacity = 0.04 + Math.min(firing * 2, 0.15);
                glow.material.size = 0.035 + firing * 0.05;
            }

            // Update label
            const label = this.labels[name];
            if (label) {
                const firingSpan = label.querySelector('.rl-firing');
                if (firingSpan) {
                    const rate = regionInfo.firing_rate || firing || 0;
                    firingSpan.textContent = `firing ${(rate * 100).toFixed(1)}%`;
                }
            }
        }

        // Spawn signal particles proportional to activity
        // More firing = more signals flying between connected regions
        for (const [a, b] of CONNECTIONS) {
            const firingA = regionFiring[a] || 0;
            const firingB = regionFiring[b] || 0;
            const activity = (firingA + firingB) / 2;

            // Probability of spawning a signal this frame scales with activity
            if (activity > 0.005 && Math.random() < activity * 2) {
                const colorA = REGION_COLORS[a] || 0xffffff;
                const colorB = REGION_COLORS[b] || 0xffffff;
                // Pick color from the more active region
                const signalColor = firingA > firingB ? colorA : colorB;
                this._spawnSignal(a, b, signalColor);
            }
        }
    }

    // ===================== ANIMATION =====================

    animate(dt) {
        this.time += dt;

        const regionFiring = this.lastState?.region_firing || {};

        for (const [name, mesh] of Object.entries(this.regionMeshes)) {
            const firing = regionFiring[name] || 0;
            const phase = this.time * 0.8 + (name.length * 0.5);
            const pulse = Math.sin(phase) * 0.1 + 0.9;
            mesh.material.opacity *= pulse;

            // Jitter neurons when active — "thinking" motion
            if (firing > 0.005 && this._basePositions[name]) {
                const positions = mesh.geometry.attributes.position.array;
                const base = this._basePositions[name];
                const jitterAmount = Math.min(firing * 0.15, 0.02);

                // Only jitter a subset each frame for performance
                const stride = Math.max(1, Math.floor(positions.length / (300 * 3)));
                for (let i = 0; i < positions.length; i += stride * 3) {
                    positions[i]     = base[i]     + (Math.random() - 0.5) * jitterAmount;
                    positions[i + 1] = base[i + 1] + (Math.random() - 0.5) * jitterAmount;
                    positions[i + 2] = base[i + 2] + (Math.random() - 0.5) * jitterAmount;
                }
                mesh.geometry.attributes.position.needsUpdate = true;
            }

            const glow = this.regionGlows[name];
            if (glow) {
                glow.material.opacity *= (Math.sin(phase + 1) * 0.1 + 0.9);
            }
        }

        // Update signal particles
        this._updateSignals(dt);

        // Update label positions
        this._updateLabelPositions();

        this.controls.update();
        this.composer.render();
    }

    _updateLabelPositions() {
        const width = window.innerWidth;
        const height = window.innerHeight;

        for (const [name, label] of Object.entries(this.labels)) {
            const layout = REGION_LAYOUT[name];
            if (!layout) continue;

            const pos = new THREE.Vector3(layout.x, layout.y, layout.z);
            pos.project(this.camera);

            const x = (pos.x * 0.5 + 0.5) * width;
            const y = (-pos.y * 0.5 + 0.5) * height;

            if (pos.z > 1) {
                label.style.display = 'none';
            } else {
                label.style.display = 'block';
                label.style.left = `${x}px`;
                label.style.top = `${y}px`;
                label.style.transform = 'translate(-50%, -100%) translateY(-10px)';
                const dist = this.camera.position.distanceTo(new THREE.Vector3(layout.x, layout.y, layout.z));
                label.style.opacity = Math.max(0, Math.min(1, 1 - (dist - 2) / 6));
            }
        }
    }

    _onResize() {
        const w = window.innerWidth, h = window.innerHeight;
        this.camera.aspect = w / h;
        this.camera.updateProjectionMatrix();
        this.renderer.setSize(w, h);
        this.composer.setSize(w, h);
    }

    _onClick(event) {
        const rect = this.renderer.domElement.getBoundingClientRect();
        this.mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
        this.mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
        this.raycaster.setFromCamera(this.mouse, this.camera);
        const intersects = this.raycaster.intersectObjects(Object.values(this.regionMeshes));
        if (intersects.length > 0) {
            const info = intersects[0].object.userData;
            window.dispatchEvent(new CustomEvent('regionClick', {
                detail: {
                    name: info.regionName,
                    color: (REGION_COLORS[info.regionName] || 0xffffff).toString(16).padStart(6, '0'),
                    description: REGION_DESCRIPTIONS[info.regionName] || `Neural region: ${info.regionName}`,
                    count: info.count,
                }
            }));
        }
    }
}
