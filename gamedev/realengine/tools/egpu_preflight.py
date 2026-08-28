"""eGPU preflight for a first Unity or Unreal run.

Run this on the machine with the eGPU attached, before installing an engine:

    python3 egpu_preflight.py

An external GPU is not a plug-and-play performance upgrade. Every failure mode
below is one where the hardware is working perfectly, the enclosure lights are
on, Device Manager shows the card, and the engine still runs on the wrong GPU or
at a fraction of the expected speed. They are silent: nothing errors, the frame
rate is just bad.

The four that actually bite, in the order they cost people time:

1. **The application picks the integrated GPU.** Windows chooses a GPU per
   application, and the default heuristic frequently picks the iGPU for an
   editor process. The eGPU sits idle at 2% while the frame rate is terrible and
   Task Manager cheerfully shows a GPU with the right name in the list.

2. **The display is attached to the laptop's internal panel.** If the eGPU
   renders but the image is shown on the built-in screen, every finished frame
   has to be copied back across Thunderbolt. That readback runs against the
   direction of the bus and typically costs 20-50% of the frame rate. A monitor
   plugged into the enclosure removes it entirely.

3. **The Thunderbolt link is not running at full width.** TB3/TB4 gives PCIe
   3.0 x4, roughly 32 Gbit/s, against x16 for a desktop slot. That is survivable
   and expected. What is not expected is negotiating x1 or dropping to PCIe 1.1
   because of a cable, a dock in the chain, or a BIOS setting -- an order of
   magnitude worse, with no error anywhere.

4. **Resizable BAR / above-4G decoding disabled.** Usually a BIOS default. Costs
   a chunk of throughput on modern cards and is invisible without checking.

This script only reads. It changes no settings, installs nothing, and touches no
files. Anything it cannot determine is reported as UNKNOWN rather than guessed:
a preflight that invents a reassuring answer is worse than no preflight.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Optional

__all__ = ["main", "Gpu", "Check", "classify_gpu", "parse_lspci_vga", "parse_nvidia_smi", "parse_windows_json"]

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
INFO = "INFO"
SKIP = "SKIP"
UNKNOWN = "UNKNOWN"

# Substrings identifying an integrated GPU. Matching on the marketing name is
# crude but is the only signal available uniformly across platforms; the PCI
# vendor:device pair would be better and is not exposed the same way on Windows.
_INTEGRATED_MARKERS = (
    "uhd graphics",
    "hd graphics",
    "iris",
    "intel(r) graphics",
    "radeon(tm) graphics",
    "radeon vega",
    "vega 3",
    "vega 6",
    "vega 7",
    "vega 8",
    "vega 10",
    "vega 11",
    "microsoft basic display",
    "llvmpipe",
    "softpipe",
    "swiftshader",
    "apple m",
)


@dataclass
class Gpu:
    """One display adapter."""

    name: str
    vendor: str = "unknown"
    vram_mb: Optional[int] = None
    driver: Optional[str] = None
    bus_id: Optional[str] = None
    integrated: Optional[bool] = None
    external: Optional[bool] = None
    driving_display: Optional[bool] = None

    def describe(self) -> str:
        bits = [self.name]
        if self.vram_mb:
            bits.append(f"{self.vram_mb / 1024:.1f} GB")
        if self.driver:
            bits.append(f"driver {self.driver}")
        if self.bus_id:
            bits.append(self.bus_id)
        return "  |  ".join(bits)


@dataclass
class Check:
    """One preflight result."""

    name: str
    status: str
    detail: str = ""
    remedy: str = ""


@dataclass
class Report:
    platform_name: str = ""
    gpus: list[Gpu] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "", remedy: str = "") -> None:
        self.checks.append(Check(name, status, detail, remedy))

    @property
    def worst(self) -> str:
        for level in (FAIL, WARN, UNKNOWN):
            if any(check.status == level for check in self.checks):
                return level
        return PASS


# -- pure parsing (unit tested) -------------------------------------------


def classify_gpu(name: str) -> tuple[str, bool]:
    """Return ``(vendor, is_integrated)`` for an adapter name."""
    lowered = name.lower()

    if "nvidia" in lowered or "geforce" in lowered or "quadro" in lowered or "rtx" in lowered:
        vendor = "nvidia"
    elif "amd" in lowered or "radeon" in lowered:
        vendor = "amd"
    elif "intel" in lowered:
        vendor = "intel"
    elif "apple" in lowered:
        vendor = "apple"
    else:
        vendor = "unknown"

    integrated = any(marker in lowered for marker in _INTEGRATED_MARKERS)

    # An Arc A-series is discrete despite being Intel, and the word "Graphics"
    # in its name would otherwise trip the integrated markers.
    if vendor == "intel" and re.search(r"\barc\b", lowered):
        integrated = False

    return vendor, integrated


def parse_lspci_vga(text: str) -> list[Gpu]:
    """Parse ``lspci`` output, keeping display controllers."""
    gpus: list[Gpu] = []
    for line in text.splitlines():
        if not re.search(r"VGA compatible controller|3D controller|Display controller", line):
            continue
        match = re.match(r"^(\S+)\s+[^:]+:\s*(.+)$", line.strip())
        if not match:
            continue
        bus_id, name = match.group(1), match.group(2).strip()
        name = re.sub(r"\s*\(rev [0-9a-f]+\)\s*$", "", name)
        vendor, integrated = classify_gpu(name)
        gpus.append(Gpu(name=name, vendor=vendor, bus_id=bus_id, integrated=integrated))
    return gpus


def parse_nvidia_smi(text: str) -> list[Gpu]:
    """Parse ``nvidia-smi --query-gpu=... --format=csv,noheader,nounits``."""
    gpus: list[Gpu] = []
    for line in text.strip().splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 4 or not parts[0]:
            continue
        name, memory, driver, bus_id = parts[0], parts[1], parts[2], parts[3]
        try:
            vram = int(float(memory))
        except ValueError:
            vram = None
        gpus.append(
            Gpu(name=name, vendor="nvidia", vram_mb=vram, driver=driver, bus_id=bus_id, integrated=False)
        )
    return gpus


def parse_windows_json(text: str) -> list[Gpu]:
    """Parse ``Get-CimInstance Win32_VideoController | ConvertTo-Json``.

    ConvertTo-Json emits a bare object rather than a list when there is exactly
    one adapter, which is the case on a laptop before the enclosure is plugged
    in -- so this has to handle both shapes or it fails precisely when the
    result matters most.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []

    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []

    gpus: list[Gpu] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("Name") or "").strip()
        if not name:
            continue
        vendor, integrated = classify_gpu(name)

        # AdapterRAM is a 32-bit field, so anything above 4 GB wraps and reports
        # nonsense -- commonly 4293918720 for an 8, 12 or 24 GB card. Treat a
        # value at or just below the 4 GB ceiling as unknown rather than
        # reporting a number that is confidently wrong.
        vram_mb: Optional[int] = None
        raw_ram = entry.get("AdapterRAM")
        if isinstance(raw_ram, (int, float)) and raw_ram > 0:
            megabytes = int(raw_ram) // (1024 * 1024)
            vram_mb = None if megabytes >= 4000 else megabytes

        driving = None
        horizontal = entry.get("CurrentHorizontalResolution")
        if horizontal is not None:
            driving = bool(horizontal)

        gpus.append(
            Gpu(
                name=name,
                vendor=vendor,
                vram_mb=vram_mb,
                driver=str(entry.get("DriverVersion") or "") or None,
                bus_id=str(entry.get("PNPDeviceID") or "") or None,
                integrated=integrated,
                driving_display=driving,
            )
        )
    return gpus


def summarise_gpus(gpus: list[Gpu]) -> tuple[list[Gpu], list[Gpu]]:
    """Split adapters into ``(discrete, integrated)``."""
    discrete = [gpu for gpu in gpus if gpu.integrated is False]
    integrated = [gpu for gpu in gpus if gpu.integrated is True]
    return discrete, integrated


# -- probes (platform dependent) ------------------------------------------


def _run(command: list[str], timeout: int = 20) -> Optional[str]:
    """Run a command, returning stdout, or None if it is unavailable."""
    if not command or shutil.which(command[0]) is None:
        return None
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def _powershell(script: str) -> Optional[str]:
    for executable in ("pwsh", "powershell.exe", "powershell"):
        if shutil.which(executable) is None:
            continue
        output = _run([executable, "-NoProfile", "-NonInteractive", "-Command", script], timeout=45)
        if output is not None:
            return output
    return None


def detect_platform() -> str:
    system = platform.system()
    if system == "Linux":
        release = platform.release().lower()
        if "microsoft" in release or os.path.exists("/proc/sys/fs/binfmt_misc/WSLInterop"):
            return "wsl"
        return "linux"
    if system == "Windows":
        return "windows"
    if system == "Darwin":
        return "macos"
    return system.lower() or "unknown"


def probe_gpus(report: Report) -> None:
    target = report.platform_name
    gpus: list[Gpu] = []

    if target in ("windows", "wsl"):
        script = (
            "Get-CimInstance Win32_VideoController | "
            "Select-Object Name,AdapterRAM,DriverVersion,PNPDeviceID,"
            "CurrentHorizontalResolution | ConvertTo-Json -Compress"
        )
        output = _powershell(script)
        if output:
            gpus = parse_windows_json(output)

    if not gpus:
        lspci = _run(["lspci"])
        if lspci:
            gpus = parse_lspci_vga(lspci)

    # nvidia-smi is authoritative for VRAM and driver where it is available, so
    # let it correct anything the generic probes reported.
    smi = _run(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version,pci.bus_id",
            "--format=csv,noheader,nounits",
        ]
    )
    if smi:
        for detailed in parse_nvidia_smi(smi):
            existing = next(
                (gpu for gpu in gpus if gpu.vendor == "nvidia" and _names_match(gpu.name, detailed.name)),
                None,
            )
            if existing is None:
                gpus.append(detailed)
            else:
                existing.vram_mb = detailed.vram_mb or existing.vram_mb
                existing.driver = detailed.driver or existing.driver
                existing.bus_id = detailed.bus_id or existing.bus_id

    report.gpus = gpus

    if not gpus:
        report.add(
            "GPU enumeration",
            FAIL,
            "no display adapters detected",
            "If this ran inside a container or a VM without GPU passthrough, run it on the host instead. "
            "Otherwise the enclosure is not being enumerated at all: reseat the Thunderbolt cable and "
            "confirm the GPU appears in Device Manager (Windows) or lspci (Linux).",
        )
        return

    discrete, integrated = summarise_gpus(gpus)
    detail = "; ".join(gpu.describe() for gpu in gpus)

    if discrete:
        report.add("GPU enumeration", PASS, detail)
    elif integrated:
        report.add(
            "GPU enumeration",
            FAIL,
            f"only integrated graphics found: {detail}",
            "The eGPU is not enumerated. Check the Thunderbolt connection is approved "
            "(Windows: Thunderbolt Control Center; Linux: `boltctl list` then `boltctl enroll <uuid>`), "
            "and that the enclosure is powered before the cable is attached.",
        )
    else:
        report.add("GPU enumeration", UNKNOWN, detail, "Could not classify any adapter as discrete.")


def _names_match(left: str, right: str) -> bool:
    normalise = lambda value: re.sub(r"[^a-z0-9]", "", value.lower())
    a, b = normalise(left), normalise(right)
    return a in b or b in a


def probe_thunderbolt(report: Report) -> None:
    target = report.platform_name

    if target in ("linux", "wsl"):
        boltctl = _run(["boltctl", "list"])
        if boltctl:
            authorised = boltctl.lower().count("authorized: yes")
            report.add(
                "Thunderbolt enrolment",
                PASS if authorised else WARN,
                f"{authorised} authorised device(s)",
                "" if authorised else "Enrol the enclosure with `boltctl enroll <uuid>`.",
            )
            return
        if os.path.isdir("/sys/bus/thunderbolt/devices"):
            entries = os.listdir("/sys/bus/thunderbolt/devices")
            report.add("Thunderbolt enrolment", INFO, f"{len(entries)} device node(s) present")
            return

    if target == "windows":
        report.add(
            "Thunderbolt enrolment",
            SKIP,
            "not probed on Windows",
            "Open Thunderbolt Control Center and confirm the enclosure is set to 'Always Connect'.",
        )
        return

    report.add("Thunderbolt enrolment", SKIP, "no Thunderbolt tooling found")


def probe_link_width(report: Report) -> None:
    """Check the PCIe link the GPU actually negotiated."""
    smi = _run(
        [
            "nvidia-smi",
            "--query-gpu=name,pcie.link.gen.current,pcie.link.width.current,pcie.link.width.max",
            "--format=csv,noheader,nounits",
        ]
    )
    if not smi:
        report.add(
            "PCIe link width",
            SKIP,
            "nvidia-smi not available",
            "On an AMD card check `lspci -vv` for 'LnkSta:' and compare against 'LnkCap:'.",
        )
        return

    problems: list[str] = []
    details: list[str] = []
    for line in smi.strip().splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 4:
            continue
        name, gen, width, max_width = parts[0], parts[1], parts[2], parts[3]
        details.append(f"{name}: PCIe gen{gen} x{width} (max x{max_width})")
        try:
            if int(width) < 4:
                problems.append(f"{name} negotiated only x{width}")
        except ValueError:
            pass

    if problems:
        report.add(
            "PCIe link width",
            FAIL,
            "; ".join(problems),
            "An eGPU should negotiate x4 over Thunderbolt. Anything narrower is an order of magnitude "
            "slower and is usually a cable (use the one supplied with the enclosure, TB-certified, "
            "not a USB-C charging cable), a dock or hub in the chain, or a BIOS Thunderbolt setting.",
        )
    else:
        report.add(
            "PCIe link width",
            PASS,
            "; ".join(details) or "link width nominal",
            "x4 is expected and correct for Thunderbolt; it is roughly a quarter of a desktop x16 slot "
            "and is the normal cost of an eGPU.",
        )


def probe_display_attachment(report: Report) -> None:
    """Is a monitor plugged into the eGPU, or the laptop's internal panel?

    This is the single largest and least obvious eGPU performance factor.
    """
    driving = [gpu for gpu in report.gpus if gpu.driving_display]

    if not driving:
        report.add(
            "Display attachment",
            UNKNOWN,
            "could not determine which adapter drives a display",
            "Plug a monitor into the enclosure itself. Rendering on the eGPU while displaying on the "
            "laptop's internal panel forces every finished frame back across Thunderbolt, which "
            "typically costs 20-50% of the frame rate for no visible reason.",
        )
        return

    discrete_driving = [gpu for gpu in driving if gpu.integrated is False]
    if discrete_driving:
        report.add(
            "Display attachment",
            PASS,
            f"display driven by {discrete_driving[0].name}",
        )
    else:
        report.add(
            "Display attachment",
            WARN,
            f"display driven by {driving[0].name} (integrated)",
            "Frames rendered on the eGPU must be copied back over Thunderbolt to reach this panel, "
            "typically costing 20-50% of the frame rate. Plug a monitor into the enclosure.",
        )


def probe_graphics_apis(report: Report) -> None:
    vulkan = _run(["vulkaninfo", "--summary"], timeout=30)
    if vulkan:
        devices = re.findall(r"deviceName\s*=\s*(.+)", vulkan)
        report.add(
            "Vulkan",
            PASS if devices else WARN,
            ", ".join(name.strip() for name in devices) or "no devices reported",
            "" if devices else "Vulkan is loadable but reports no device.",
        )
    else:
        report.add(
            "Vulkan",
            SKIP,
            "vulkaninfo not installed",
            "Unreal on Linux needs Vulkan. On Windows, D3D12 is the default and Vulkan is optional.",
        )

    if report.platform_name == "windows":
        report.add(
            "Direct3D 12",
            SKIP,
            "not probed",
            "Run `dxdiag` and confirm the Display tab lists the eGPU with DDI 12.",
        )


def probe_gpu_preference(report: Report) -> None:
    """The trap that wastes the most time: the app runs on the wrong GPU."""
    if report.platform_name not in ("windows", "wsl"):
        report.add(
            "Per-application GPU preference",
            SKIP,
            "Windows-specific",
            "On Linux use DRI_PRIME or __NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia.",
        )
        return

    discrete, integrated = summarise_gpus(report.gpus)
    if discrete and integrated:
        report.add(
            "Per-application GPU preference",
            WARN,
            f"both {integrated[0].name} and {discrete[0].name} present -- Windows chooses per application",
            "Set it explicitly before the first run, or the editor may quietly use the iGPU: "
            "Settings > System > Display > Graphics, add the engine executable, set 'High performance'. "
            "Add Unity.exe, UnityHub.exe and UnrealEditor.exe separately -- the setting is per binary, "
            "and launching the editor from the Hub does not inherit the Hub's preference.",
        )
    elif discrete:
        report.add("Per-application GPU preference", PASS, "only a discrete GPU present; no ambiguity")
    else:
        report.add("Per-application GPU preference", UNKNOWN, "GPU set unclear")


def probe_headroom(report: Report) -> None:
    """Disk and memory, which both engines are unusually hungry for."""
    try:
        usage = shutil.disk_usage(os.path.expanduser("~"))
        free_gb = usage.free / (1024 ** 3)
    except OSError:
        report.add("Disk headroom", UNKNOWN, "could not stat home directory")
        return

    detail = f"{free_gb:.0f} GB free on the home volume"
    if free_gb < 50:
        report.add(
            "Disk headroom",
            FAIL,
            detail,
            "Unity plus one project needs ~30 GB; Unreal from the launcher needs ~120 GB, and a source "
            "build with a populated derived data cache can exceed 250 GB.",
        )
    elif free_gb < 150:
        report.add(
            "Disk headroom",
            WARN,
            detail,
            "Enough for Unity. Unreal from the Epic Launcher wants ~120 GB before any project.",
        )
    else:
        report.add("Disk headroom", PASS, detail)


# -- reporting ------------------------------------------------------------

_COLOURS = {PASS: "\033[32m", WARN: "\033[33m", FAIL: "\033[31m", UNKNOWN: "\033[35m", INFO: "\033[36m", SKIP: "\033[90m"}
_RESET = "\033[0m"


def _colour(status: str, use_colour: bool) -> str:
    if not use_colour:
        return status.ljust(7)
    return f"{_COLOURS.get(status, '')}{status.ljust(7)}{_RESET}"


def build_report() -> Report:
    report = Report(platform_name=detect_platform())
    probe_gpus(report)
    probe_thunderbolt(report)
    probe_link_width(report)
    probe_display_attachment(report)
    probe_graphics_apis(report)
    probe_gpu_preference(report)
    probe_headroom(report)
    return report


def main(argv: Optional[list[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    as_json = "--json" in argv
    use_colour = "--no-color" not in argv and sys.stdout.isatty()

    report = build_report()

    if as_json:
        print(
            json.dumps(
                {
                    "platform": report.platform_name,
                    "gpus": [gpu.__dict__ for gpu in report.gpus],
                    "checks": [check.__dict__ for check in report.checks],
                    "verdict": report.worst,
                },
                indent=2,
            )
        )
        return 0 if report.worst in (PASS, WARN) else 1

    print(f"eGPU preflight  --  platform: {report.platform_name}\n")

    if report.gpus:
        print("Adapters:")
        for gpu in report.gpus:
            kind = "integrated" if gpu.integrated else ("discrete" if gpu.integrated is False else "unclassified")
            print(f"  [{kind}] {gpu.describe()}")
        print()

    for check in report.checks:
        print(f"  {_colour(check.status, use_colour)} {check.name}")
        if check.detail:
            print(f"          {check.detail}")
        if check.remedy and check.status in (WARN, FAIL, UNKNOWN, SKIP):
            for line in _wrap(check.remedy, 86):
                print(f"          -> {line}")
    print()

    verdict = report.worst
    if verdict == PASS:
        print("Ready. Next: gamedev/realengine/EGPU_FIRST_RUN.md, step 3.")
    elif verdict == WARN:
        print("Usable, but fix the warnings above first -- each one costs frame rate silently.")
    elif verdict == UNKNOWN:
        print("Some checks could not be determined. Resolve them before trusting a benchmark.")
    else:
        print("Not ready. Address the FAIL items above.")

    return 0 if verdict in (PASS, WARN) else 1


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


if __name__ == "__main__":
    raise SystemExit(main())
