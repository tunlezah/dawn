# Dawn

A bedside DAB+ alarm clock radio for Raspberry Pi. See the sections below for
hardware, first boot, and troubleshooting. (Filled in progressively; the final
version lands in phase 10.)

## Quick start (laptop simulator)

```bash
make setup      # creates .venv, installs the Python packages and web deps
make sim        # runs simulators + core + web on http://localhost:8080
```

Open http://localhost:8080/ for the control UI and http://localhost:8080/face
for the face. Sim controls (lux, GPS, DAB sync, inputs) live on
http://localhost:8099/.
