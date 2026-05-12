/**
 * NeuralDust - Floating particle system that creates ambient neural atmosphere.
 * Drifting glow particles around the brain, reacting to global activity.
 */
import * as THREE from 'three';

export class NeuralDust {
    constructor(scene, count = 3000) {
        this.count = count;
        this.scene = scene;

        const geometry = new THREE.BufferGeometry();
        const positions = new Float32Array(count * 3);
        const colors = new Float32Array(count * 3);
        const velocities = new Float32Array(count * 3);

        const palette = [
            new THREE.Color(0x00ffff),
            new THREE.Color(0x4488ff),
            new THREE.Color(0x00ff88),
            new THREE.Color(0xff00ff),
            new THREE.Color(0xffcc00),
            new THREE.Color(0xff6600),
        ];

        for (let i = 0; i < count; i++) {
            // Distribute particles in a large sphere around the brain
            const r = 0.8 + Math.random() * 2.5;
            const theta = Math.random() * Math.PI * 2;
            const phi = Math.acos(2 * Math.random() - 1);

            positions[i * 3]     = r * Math.sin(phi) * Math.cos(theta);
            positions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta) * 0.7 + 0.1;
            positions[i * 3 + 2] = r * Math.cos(phi);

            // Slow drift velocities
            velocities[i * 3]     = (Math.random() - 0.5) * 0.008;
            velocities[i * 3 + 1] = (Math.random() - 0.5) * 0.005;
            velocities[i * 3 + 2] = (Math.random() - 0.5) * 0.008;

            // Random color from palette
            const c = palette[Math.floor(Math.random() * palette.length)];
            colors[i * 3]     = c.r;
            colors[i * 3 + 1] = c.g;
            colors[i * 3 + 2] = c.b;
        }

        geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
        geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
        this._velocities = velocities;

        // Create round orb texture for dust
        const texCanvas = document.createElement('canvas');
        texCanvas.width = 32;
        texCanvas.height = 32;
        const tCtx = texCanvas.getContext('2d');
        const grad = tCtx.createRadialGradient(16, 16, 0, 16, 16, 16);
        grad.addColorStop(0, 'rgba(255,255,255,1)');
        grad.addColorStop(0.3, 'rgba(255,255,255,0.5)');
        grad.addColorStop(0.7, 'rgba(255,255,255,0.1)');
        grad.addColorStop(1, 'rgba(255,255,255,0)');
        tCtx.fillStyle = grad;
        tCtx.fillRect(0, 0, 32, 32);
        const dustTexture = new THREE.CanvasTexture(texCanvas);

        const material = new THREE.PointsMaterial({
            size: 0.008,
            map: dustTexture,
            transparent: true,
            opacity: 0.25,
            blending: THREE.AdditiveBlending,
            depthWrite: false,
            sizeAttenuation: true,
            vertexColors: true,
        });

        this.mesh = new THREE.Points(geometry, material);
        scene.add(this.mesh);
    }

    /**
     * Update particle positions and appearance.
     * @param {number} time - Elapsed time
     * @param {number} activity - Global brain activity 0-1
     */
    update(time, activity = 0.1) {
        const positions = this.mesh.geometry.attributes.position.array;
        const vel = this._velocities;

        const speed = 0.3 + activity * 2.5;

        for (let i = 0; i < this.count; i++) {
            const i3 = i * 3;

            // Drift
            positions[i3]     += vel[i3] * speed;
            positions[i3 + 1] += vel[i3 + 1] * speed;
            positions[i3 + 2] += vel[i3 + 2] * speed;

            // Gentle swirl
            const angle = time * 0.05 + i * 0.003;
            positions[i3]     += Math.sin(angle) * 0.0003;
            positions[i3 + 2] += Math.cos(angle) * 0.0003;

            // Respawn if too far
            const x = positions[i3], y = positions[i3 + 1], z = positions[i3 + 2];
            const dist = Math.sqrt(x * x + y * y + z * z);
            if (dist > 4.0) {
                const r = 0.3 + Math.random() * 0.8;
                const theta = Math.random() * Math.PI * 2;
                const phi = Math.acos(2 * Math.random() - 1);
                positions[i3]     = r * Math.sin(phi) * Math.cos(theta);
                positions[i3 + 1] = r * Math.sin(phi) * Math.sin(theta) * 0.7 + 0.1;
                positions[i3 + 2] = r * Math.cos(phi);
            }
        }

        this.mesh.geometry.attributes.position.needsUpdate = true;

        // Pulse with activity
        this.mesh.material.opacity = 0.1 + activity * 0.4;
        this.mesh.material.size = 0.004 + activity * 0.01;
    }
}
