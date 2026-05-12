"""
Claude Bridge - Connects the brain to Claude so they work together.

This module provides:
1. Brain state summaries Claude can read and act on
2. A way for Claude to send observations/context to the brain
3. Motor cortex decoding - translating brain output into useful signals
4. Activity logging so the brain builds up experience from Claude's work

The brain runs locally as a persistent service. Claude connects via the API
to read brain state, send input, and receive decoded insights.
"""

import time
import json
import os
import numpy as np
from collections import deque


def _executive_boost_for_text(text: str) -> bool:
    """Match Sygnif-structured lines for extra prefrontal drive (see ``Brain.inject_sensory_input``)."""
    t = (text or "").strip()
    return t.startswith(("SYGNIF_", "HIVEMIND_", "BYBIT_HIVEMIND_LAYER"))


class ClaudeBridge:
    """Interface between Claude and the neuromorphic brain."""

    def __init__(self, brain):
        self.brain = brain
        self._activity_log = deque(maxlen=1000)
        self._claude_inputs = deque(maxlen=100)
        self._decoded_outputs = deque(maxlen=100)
        self._session_start = time.time()
        self._interaction_count = 0

        # Motor cortex decoder state
        self._motor_decoder = MotorDecoder(brain)

        # Learning reporter - tracks what the brain has learned
        self._learning_reporter = LearningReporter(brain)

        # Knowledge store - persistent text/fact storage (Obsidian vault optional via
        # NEUROLINKED_OBSIDIAN_VAULT + sensory/obsidian_vault.py)
        from brain.knowledge_store import KnowledgeStore
        self.knowledge = KnowledgeStore()

        # Context tracking - what Claude is currently doing
        self._current_context = {
            "task": "",
            "tools_used": [],
            "last_update": 0,
        }

    def send_observation(self, observation: dict):
        """
        Claude sends an observation to the brain.
        This encodes the observation as sensory input.

        observation = {
            "type": "text"|"action"|"screen"|"context",
            "content": str or dict,
            "source": "claude"|"user"|"system"
        }
        """
        self._interaction_count += 1
        self._claude_inputs.append({
            "time": time.time(),
            **observation
        })

        obs_type = observation.get("type", "text")
        content = observation.get("content", "")

        if obs_type == "text" and isinstance(content, str):
            # Encode text as sensory input
            from sensory.text import TextEncoder
            encoder = TextEncoder(feature_dim=256)
            features = encoder.encode(content)
            self.brain.inject_sensory_input(
                "text",
                features,
                executive_boost=_executive_boost_for_text(content),
            )

            # Boost attention (acetylcholine) when Claude sends input
            self.brain.neuromodulators["acetylcholine"] = min(
                1.0, self.brain.neuromodulators["acetylcholine"] + 0.1
            )

            # AUTO-RECALL: surface related memories as neural input
            # This is real associative memory — the brain remembers related
            # things when it hears something new, just like a biological brain.
            if len(content.strip()) >= 15:
                self.auto_recall_for_input(content, limit=3)

        elif obs_type == "action":
            # Claude performed an action - boost dopamine (learning signal)
            self.brain.neuromodulators["dopamine"] = min(
                1.0, self.brain.neuromodulators["dopamine"] + 0.05
            )
            # Encode action description as text input
            if isinstance(content, str):
                from sensory.text import TextEncoder
                encoder = TextEncoder(feature_dim=256)
                features = encoder.encode(f"ACTION: {content}")
                self.brain.inject_sensory_input("text", features)

        elif obs_type == "context":
            # Update what Claude is currently working on
            if isinstance(content, dict):
                self._current_context.update(content)
                self._current_context["last_update"] = time.time()
            # Context changes boost norepinephrine (arousal/alertness)
            self.brain.neuromodulators["norepinephrine"] = min(
                1.0, self.brain.neuromodulators["norepinephrine"] + 0.05
            )

        # Record input for learning reporter
        label = str(content)[:100] if content else "unknown"
        self._learning_reporter.record_input(label, observation.get("source", "claude"))

        # Store full text in knowledge store for retrieval
        source = observation.get("source", "claude")
        if isinstance(content, str) and content.strip():
            try:
                self.knowledge.store(
                    text=content,
                    source=source,
                    metadata={"type": obs_type, "step": self.brain.step_count}
                )
            except Exception as e:
                print(f"[KNOWLEDGE] Store error: {e}")
        elif isinstance(content, dict):
            # Store dict content as JSON text
            try:
                text_repr = json.dumps(content, indent=2)
                self.knowledge.store(
                    text=text_repr,
                    source=source,
                    tags=list(content.keys())[:5],
                    metadata={"type": obs_type, "step": self.brain.step_count}
                )
            except Exception as e:
                print(f"[KNOWLEDGE] Store error: {e}")

        self._activity_log.append({
            "time": time.time(),
            "type": "input",
            "obs_type": obs_type,
            "source": observation.get("source", "claude"),
        })

    # =================== KNOWLEDGE RETRIEVAL ===================

    def recall(self, query: str, limit: int = 10) -> list:
        """Recall knowledge about a topic. Uses semantic search (TF-IDF + cosine)
        for real associative memory — finds conceptually related memories,
        not just exact keyword matches."""
        # Try semantic search first (real associative memory)
        try:
            results = self.knowledge.semantic_search(query, limit=limit)
            if results:
                return results
        except Exception as e:
            print(f"[BRIDGE] Semantic search error: {e}")
        # Fallback to keyword-based recall
        return self.knowledge.recall(query, limit=limit)

    def search_knowledge(self, query: str, limit: int = 20) -> list:
        """Full-text search across all stored knowledge."""
        return self.knowledge.search(query, limit=limit)

    def auto_recall_for_input(self, text: str, limit: int = 3):
        """
        Auto-recall: when a new observation comes in, find related memories
        and make the brain 'remember' them by injecting their content as neural
        input. This is real associative memory in action.
        """
        try:
            related = self.knowledge.associate(text, limit=limit)
            if not related:
                return
            from sensory.text import TextEncoder
            encoder = TextEncoder(feature_dim=256)
            for mem in related:
                # Re-encode the related memory as a faint neural signal
                # (weaker than live input - this is "remembering" not "seeing")
                features = encoder.encode(mem["text"][:200])
                if features.size > 0:
                    # Scale down - recalled memories are quieter than real input
                    features = features * 0.4
                    self.brain.inject_sensory_input("text", features)
            return related
        except Exception as e:
            print(f"[BRIDGE] Auto-recall error: {e}")
            return []

    def get_recent_knowledge(self, limit: int = 20) -> list:
        """Get the most recently stored knowledge entries."""
        return self.knowledge.recent(limit=limit)

    def get_knowledge_stats(self) -> dict:
        """Get stats about the knowledge store."""
        return self.knowledge.get_stats()

    def store_knowledge(self, text: str, source: str = "claude", tags: list = None) -> int:
        """Directly store a piece of knowledge."""
        return self.knowledge.store(text=text, source=source, tags=tags)

    # =================== BRAIN STATE ===================

    def get_brain_summary(self):
        """
        Get a concise summary of brain state that Claude can use.
        This is the primary way Claude reads the brain.

        Avoids ``brain.get_state()`` (full per-region serialization for the dashboard) so
        ``GET /api/sygnif/summary`` stays fast under load — Sygnif CLI and hooks poll this path.
        """
        b = self.brain
        decoded = self._motor_decoder.decode()

        firing = {
            name: float(region.neurons.get_firing_rate() * 100)
            for name, region in b.regions.items()
        }
        sorted_regions = sorted(firing.items(), key=lambda x: x[1], reverse=True)
        top_regions = sorted_regions[:3]

        neuro = b.neuromodulators
        attention = neuro.get("acetylcholine", 0.5)
        learning_rate = neuro.get("dopamine", 0.5)
        arousal = neuro.get("norepinephrine", 0.3)
        calm = neuro.get("serotonin", 0.5)

        hippo = b.regions.get("hippocampus")
        memories = len(hippo.memory_buffer) if hippo else 0
        replaying = bool(getattr(hippo, "replay_mode", False)) if hippo else False

        pred = b.regions.get("predictive")
        surprise = float(getattr(pred, "surprise", 0.0)) if pred else 0.0

        safety = b.safety.get_state() if getattr(b, "safety", None) else {}

        return {
            "step": b.step_count,
            "stage": b.development_stage,
            "uptime": round(time.time() - b.start_time, 1),
            "performance": round(b.steps_per_second, 2),
            "attention_level": round(attention, 3),
            "learning_rate": round(learning_rate, 3),
            "arousal": round(arousal, 3),
            "calm": round(calm, 3),
            "surprise": round(surprise, 3),
            "top_active_regions": [
                {"name": name, "activity": round(pct, 1)} for name, pct in top_regions
            ],
            "memories_stored": memories,
            "replaying_memories": replaying,
            "motor_output": decoded,
            "interaction_count": self._interaction_count,
            "current_context": self._current_context,
            "safety": safety,
        }

    def get_insights(self):
        """
        Get brain-derived insights that could be useful to Claude.
        Translates neural patterns into actionable information.
        """
        insights = []

        # High surprise = something unexpected, might need attention
        pred = self.brain.regions.get("predictive")
        if pred and pred.surprise > 0.6:
            insights.append({
                "type": "high_surprise",
                "message": "Brain detecting high novelty - current input differs significantly from learned patterns",
                "level": round(pred.surprise, 2),
            })

        # Low energy = brain has been working hard
        brainstem = self.brain.regions.get("brainstem")
        if brainstem and brainstem.energy < 0.3:
            insights.append({
                "type": "low_energy",
                "message": "Brain energy is low - high sustained activity detected",
                "level": round(brainstem.energy, 2),
            })

        # Memory replay = brain is consolidating what it learned
        hippo = self.brain.regions.get("hippocampus")
        if hippo and hippo.replay_mode:
            insights.append({
                "type": "memory_replay",
                "message": f"Brain is replaying and consolidating {len(hippo.memory_buffer)} stored memories",
            })

        # Reflex arc active = urgent stimulus detected
        reflex = self.brain.regions.get("reflex_arc")
        if reflex and reflex.reflex_active:
            insights.append({
                "type": "reflex_active",
                "message": "Reflex arc triggered - high-urgency stimulus detected",
            })

        # Prefrontal working memory load
        prefrontal = self.brain.regions.get("prefrontal")
        if prefrontal:
            wm_load = float(np.mean(prefrontal.working_memory > 0.1))
            if wm_load > 0.3:
                insights.append({
                    "type": "high_working_memory",
                    "message": f"Working memory is {round(wm_load * 100)}% loaded - brain is actively maintaining context",
                    "load": round(wm_load, 2),
                })

        return insights

    def get_learned_patterns(self):
        """Get structured report of what the brain has learned."""
        return self._learning_reporter.get_learned_patterns()

    def get_learning_summary(self):
        """Get plain-English summary of what the brain has learned."""
        return self._learning_reporter.get_learning_summary_text()

    def get_activity_log(self, limit=50):
        """Get recent activity log entries."""
        return list(self._activity_log)[-limit:]

    def get_state(self):
        """Full bridge state for API."""
        return {
            "connected": True,
            "session_uptime": round(time.time() - self._session_start, 1),
            "interaction_count": self._interaction_count,
            "pending_inputs": len(self._claude_inputs),
            "current_context": self._current_context,
            "screen_observer": None,  # Filled by server if active
        }


class LearningReporter:
    """Analyzes brain state and reports what it has learned in human-readable form."""

    def __init__(self, brain):
        self.brain = brain
        # Track what inputs were fed and how the brain responded
        self._input_history = deque(maxlen=500)
        self._pattern_labels = {}  # Maps neural fingerprints to input descriptions
        self._association_map = {}  # Tracks which inputs activate similar neurons

    def record_input(self, label: str, source: str = "unknown"):
        """Record an input and capture the brain's response fingerprint."""
        # Capture which regions are most active right now
        fingerprint = {}
        for name, region in self.brain.regions.items():
            rate = region.neurons.get_firing_rate()
            if rate > 0.01:
                fingerprint[name] = round(float(rate), 4)

        # Capture concept layer winners (the abstract representation)
        concept = self.brain.regions.get("concept_layer")
        concept_code = []
        if concept:
            fired = concept.neurons.fired
            if np.any(fired):
                concept_code = np.where(fired)[0].tolist()[:20]

        # Capture association cortex binding pattern
        assoc = self.brain.regions.get("association")
        binding_hot = []
        if assoc and hasattr(assoc, "binding_strength"):
            top_bound = np.argsort(assoc.binding_strength)[-10:]
            binding_hot = top_bound.tolist()

        entry = {
            "time": time.time(),
            "label": label,
            "source": source,
            "step": self.brain.step_count,
            "fingerprint": fingerprint,
            "concept_code": concept_code,
            "binding_neurons": binding_hot,
            "neuromodulators": {k: round(float(v), 3) for k, v in self.brain.neuromodulators.items()},
        }
        self._input_history.append(entry)

        # Track associations - inputs that activate similar concept neurons
        if concept_code:
            code_key = str(sorted(concept_code[:5]))
            if code_key not in self._association_map:
                self._association_map[code_key] = []
            self._association_map[code_key].append(label)
            # Keep only recent entries per group
            if len(self._association_map[code_key]) > 20:
                self._association_map[code_key] = self._association_map[code_key][-20:]

    def get_learned_patterns(self):
        """Report what the brain has learned - grouped associations and patterns."""
        report = {
            "total_inputs_processed": len(self._input_history),
            "development_stage": self.brain.development_stage,
            "total_steps": self.brain.step_count,
            "associations": [],
            "strongest_pathways": [],
            "memory_summary": {},
            "synapse_growth": {},
            "region_specialization": {},
        }

        # 1. Discovered associations - inputs grouped by similar neural response
        for code_key, labels in self._association_map.items():
            if len(labels) >= 2:
                unique_labels = list(dict.fromkeys(labels))  # Dedupe preserving order
                if len(unique_labels) >= 2:
                    report["associations"].append({
                        "group": unique_labels[-5:],  # Last 5 unique
                        "times_co_activated": len(labels),
                        "neural_code": code_key,
                    })

        # Sort by how often they co-activate
        report["associations"].sort(key=lambda x: x["times_co_activated"], reverse=True)
        report["associations"] = report["associations"][:10]  # Top 10

        # 2. Strongest pathways - which inter-region connections have grown strongest
        for (src, dst), syn in self.brain.connections.items():
            if syn.nnz > 0:
                stats = syn.get_stats()
                report["strongest_pathways"].append({
                    "from": src,
                    "to": dst,
                    "mean_weight": round(stats["mean_weight"], 4),
                    "max_weight": round(stats["max_weight"], 4),
                    "synapse_count": stats["count"],
                })

        # Sort by mean weight (strongest learned connections first)
        report["strongest_pathways"].sort(key=lambda x: x["mean_weight"], reverse=True)
        report["strongest_pathways"] = report["strongest_pathways"][:10]

        # 3. Memory summary from hippocampus
        hippo = self.brain.regions.get("hippocampus")
        if hippo:
            report["memory_summary"] = {
                "memories_stored": len(hippo.memory_buffer),
                "max_capacity": hippo.max_memories,
                "replay_active": hippo.replay_mode,
                "memory_utilization": f"{len(hippo.memory_buffer) / hippo.max_memories * 100:.1f}%",
            }

        # 4. Synapse growth - how much have weights changed from initial
        total_synapses = 0
        total_weight = 0.0
        for (src, dst), syn in self.brain.connections.items():
            if syn.nnz > 0:
                total_synapses += syn.nnz
                total_weight += float(np.sum(syn.weights.data))
        avg_weight = total_weight / max(total_synapses, 1)
        report["synapse_growth"] = {
            "total_synapses": total_synapses,
            "average_weight": round(avg_weight, 4),
            "initial_average": 0.5,  # From log-normal init
            "growth_percent": round((avg_weight - 0.5) / 0.5 * 100, 1),
        }

        # 5. Region specialization - which regions are most active
        for name, region in self.brain.regions.items():
            history = region.activity_history
            if history:
                avg_rate = float(np.mean(history))
                peak_rate = float(np.max(history))
            else:
                avg_rate = 0.0
                peak_rate = 0.0

            extra = {}
            if name == "predictive" and hasattr(region, "surprise"):
                extra["current_surprise"] = round(float(region.surprise), 3)
            if name == "prefrontal" and hasattr(region, "working_memory"):
                wm_usage = float(np.mean(region.working_memory > 0.1))
                extra["working_memory_usage"] = f"{wm_usage * 100:.0f}%"
            if name == "association" and hasattr(region, "binding_strength"):
                strong_bindings = int(np.sum(region.binding_strength > 0.5))
                extra["strong_associations"] = strong_bindings
            if name == "brainstem":
                extra["energy"] = round(float(region.energy), 3)
                extra["arousal"] = round(float(region.arousal), 3)

            report["region_specialization"][name] = {
                "neurons": region.n_neurons,
                "avg_firing_rate": f"{avg_rate * 100:.2f}%",
                "peak_firing_rate": f"{peak_rate * 100:.2f}%",
                **extra,
            }

        return report

    def get_learning_summary_text(self):
        """Get a plain-English summary of what the brain has learned."""
        report = self.get_learned_patterns()
        lines = []

        lines.append(f"=== NeuroLinked Brain Learning Report ===")
        lines.append(f"Stage: {report['development_stage']} | Steps: {report['total_steps']:,} | Inputs: {report['total_inputs_processed']}")
        lines.append("")

        # Synapse growth
        sg = report["synapse_growth"]
        direction = "strengthened" if sg["growth_percent"] > 0 else "weakened"
        lines.append(f"Synapses: {sg['total_synapses']:,} connections, avg weight {sg['average_weight']:.4f} ({direction} {abs(sg['growth_percent']):.1f}% from initial)")

        # Memory
        ms = report.get("memory_summary", {})
        if ms:
            lines.append(f"Memory: {ms.get('memories_stored', 0)} patterns stored ({ms.get('memory_utilization', '0%')} capacity)")

        # Associations
        assocs = report.get("associations", [])
        if assocs:
            lines.append("")
            lines.append("Discovered Associations (inputs that activate similar neurons):")
            for i, a in enumerate(assocs[:5]):
                group = ", ".join(f'"{g}"' for g in a["group"][:3])
                lines.append(f"  Group {i+1}: [{group}] (co-activated {a['times_co_activated']}x)")
        else:
            lines.append("")
            lines.append("No associations discovered yet - feed the brain more varied inputs!")

        # Strongest pathways
        paths = report.get("strongest_pathways", [])
        if paths:
            lines.append("")
            lines.append("Strongest Learned Pathways:")
            for p in paths[:5]:
                lines.append(f"  {p['from']} -> {p['to']}: weight {p['mean_weight']:.4f} ({p['synapse_count']:,} synapses)")

        # Active regions
        lines.append("")
        lines.append("Region Activity:")
        for name, info in report.get("region_specialization", {}).items():
            extras = ""
            if "strong_associations" in info:
                extras += f" | {info['strong_associations']} strong bindings"
            if "working_memory_usage" in info:
                extras += f" | WM: {info['working_memory_usage']}"
            if "current_surprise" in info:
                extras += f" | surprise: {info['current_surprise']}"
            lines.append(f"  {name}: {info['avg_firing_rate']} avg firing ({info['neurons']:,} neurons){extras}")

        return "\n".join(lines)


class MotorDecoder:
    """Decodes motor cortex firing patterns into meaningful output signals."""

    def __init__(self, brain):
        self.brain = brain
        self._output_history = deque(maxlen=50)

    def decode(self):
        """
        Read motor cortex and decode into output signals.
        Returns a dict of decoded outputs.
        """
        motor = self.brain.regions.get("motor_cortex")
        if not motor:
            return {"active": False}

        output = motor.motor_output
        rate = motor.neurons.get_firing_rate()
        n = motor.n_neurons

        # Divide motor cortex into output channels
        quarter = n // 4

        # Channel 1: Action intensity (0-1)
        action_intensity = float(np.mean(output[:quarter]))

        # Channel 2: Direction/valence (-1 to 1, approach vs avoid)
        approach = float(np.mean(output[quarter:2 * quarter]))
        avoid = float(np.mean(output[2 * quarter:3 * quarter]))
        valence = approach - avoid

        # Channel 3: Communication drive (desire to output)
        comm_drive = float(np.mean(output[3 * quarter:]))

        decoded = {
            "active": bool(rate > 0.01),
            "firing_rate": round(float(rate), 4),
            "action_intensity": round(float(action_intensity), 4),
            "valence": round(float(np.clip(valence, -1, 1)), 4),
            "communication_drive": round(float(comm_drive), 4),
        }

        self._output_history.append(decoded)
        return decoded
