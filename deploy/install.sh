#!/usr/bin/env bash
# Dawn installer for Raspberry Pi OS (Bookworm / Trixie, 64-bit or 32-bit). Idempotent.
#
#   sudo ./deploy/install.sh                 full install (builds rtl-sdr-blog, welle.io, shairport-sync + nqptp)
#   sudo ./deploy/install.sh --update        after `git pull`: reinstall python/web/configs, skip finished builds
#   sudo ./deploy/install.sh --audio hifiberry   I2S amp (Pimoroni Audio Amp SHIM / HiFiBerry): hifiberry-dac overlay,
#                                                onboard audio off, mono EQ chain, output pinned (default: auto)
#   sudo ./deploy/install.sh --display hyperpixel4|waveshare_dsi|hdmi   force the panel overlay (default: auto)
#   sudo ./deploy/install.sh --data-device /dev/mmcblk0p3   mount /var/lib/dawn from an ext4 partition (data=journal)
#   sudo ./deploy/install.sh --data-image-mb 1024          ...or from a loop-mounted ext4 image (data=journal)
#   sudo ./deploy/install.sh --readonly      enable the overlayfs read-only root (needs --data-device)
#   sudo ./deploy/install.sh --no-build      skip source builds (use whatever is installed)
#   sudo ./deploy/install.sh --rebuild       force rebuilding rtl-sdr / welle / shairport-sync
#   sudo ./deploy/install.sh --no-web        skip the web build (e.g. when `make deploy` ships web/dist)
#
# Everything it writes is marked or in its own file, so re-running is safe.
set -euo pipefail

DAWN_DIR=/opt/dawn
DAWN_USER=dawn
LOG=/var/log/dawn-install.log
SRC_DIR=/usr/local/src
UPDATE=0 REBUILD=0 NO_BUILD=0 NO_WEB=0 READONLY=0
DATA_DEVICE="" DATA_IMAGE_MB=0 AUDIO=auto PANEL=auto
RTLSDR_REPO=https://github.com/rtlsdrblog/rtl-sdr-blog.git
WELLE_REPO=https://github.com/AlbrechtL/welle.io.git
SPS_REPO=https://github.com/mikebrady/shairport-sync.git
NQPTP_REPO=https://github.com/mikebrady/nqptp.git

while [ $# -gt 0 ]; do
  case "$1" in
    --update) UPDATE=1;;
    --rebuild) REBUILD=1;;
    --no-build) NO_BUILD=1;;
    --no-web) NO_WEB=1;;
    --readonly) READONLY=1;;
    --audio) AUDIO="$2"; shift;;
    --display) PANEL="$2"; shift;;
    --data-device) DATA_DEVICE="$2"; shift;;
    --data-image-mb) DATA_IMAGE_MB="$2"; shift;;
    -h|--help) sed -n '2,20p' "$0"; exit 0;;
    *) echo "unknown option $1"; exit 2;;
  esac
  shift
done

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)"; exit 1; }
mkdir -p "$(dirname "$LOG")"
exec > >(tee -a "$LOG") 2>&1
echo "=== Dawn install $(date -Is) args: $* update=$UPDATE ==="

HERE="$(cd "$(dirname "$0")/.." && pwd)"
step() { echo; echo "--- $* ---"; }

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
MODEL="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || echo generic)"
IS_PI=0; case "$MODEL" in *"Raspberry Pi"*) IS_PI=1;; esac
LOW_POWER=0; case "$MODEL" in *"Zero 2"*|*"Pi 3"*|*"Pi 2"*) LOW_POWER=1;; esac
ARCH="$(dpkg --print-architecture)"
BOOTCFG=/boot/firmware/config.txt; [ -f "$BOOTCFG" ] || BOOTCFG=/boot/config.txt
CODENAME="$(. /etc/os-release; echo "${VERSION_CODENAME:-unknown}")"

detect_panel() {
  [ "$PANEL" != auto ] && { echo "$PANEL"; return; }
  for c in /sys/class/drm/card*-DSI-*; do [ -e "$c/status" ] && grep -q '^connected' "$c/status" && { echo waveshare_dsi; return; }; done
  for c in /sys/class/drm/card*-DPI-*; do [ -e "$c/status" ] && grep -q '^connected' "$c/status" && { echo hyperpixel4; return; }; done
  grep -qs 'hyperpixel4' "$BOOTCFG" && { echo hyperpixel4; return; }
  grep -qsE 'waveshare-panel|vc4-kms-dsi-7inch' "$BOOTCFG" && { echo waveshare_dsi; return; }
  for c in /sys/class/drm/card*-HDMI-*; do [ -e "$c/status" ] && grep -q '^connected' "$c/status" && { echo hdmi; return; }; done
  case "$MODEL" in *"Zero 2"*) echo hdmi;; *) echo waveshare_dsi;; esac   # Zero 2 W has no DSI connector
}
PANEL_DETECTED="$(detect_panel)"
# --update runs without --audio: keep an I2S amp set up by an earlier install (or already loaded)
detect_audio() {
  [ "$AUDIO" != auto ] && { echo "$AUDIO"; return; }
  awk '/^# >>> dawn >>>/{d=1} /^# <<< dawn <<</{d=0} d && /^dtoverlay=hifiberry-dac/{f=1} END{exit !f}' "$BOOTCFG" 2>/dev/null && { echo hifiberry; return; }
  grep -qsi 'hifiberry' /proc/asound/cards && { echo hifiberry; return; }
  echo auto
}
AUDIO="$(detect_audio)"
echo "model: $MODEL ($ARCH, $CODENAME) low_power=$LOW_POWER panel=$PANEL_DETECTED audio=$AUDIO"

# ---------------------------------------------------------------------------
# Packages
# ---------------------------------------------------------------------------
step "apt packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
CHROMIUM=chromium; apt-cache show chromium >/dev/null 2>&1 || CHROMIUM=chromium-browser
PKGS=(
  git curl rsync build-essential cmake pkg-config autoconf automake libtool xxd
  python3 python3-venv python3-dev python3-pip python3-systemd python3-libgpiod
  libusb-1.0-0-dev libfaad-dev libmpg123-dev libmp3lame-dev libfftw3-dev libasound2-dev
  libpopt-dev libconfig-dev libavahi-client-dev libssl-dev libsoxr-dev libplist-dev libsodium-dev
  libavutil-dev libavcodec-dev libavformat-dev uuid-dev libgcrypt-dev libpipewire-0.3-dev
  mpv pipewire pipewire-pulse pipewire-audio wireplumber libspa-0.2-bluetooth
  bluez gpsd gpsd-clients chrony avahi-daemon network-manager i2c-tools
  cage "$CHROMIUM" fonts-dejavu-core logrotate
)
apt-get install -y --no-install-recommends "${PKGS[@]}" || apt-get install -y --no-install-recommends "${PKGS[@]/libpipewire-0.3-dev/}"
apt-cache show python3-lgpio >/dev/null 2>&1 && apt-get install -y --no-install-recommends python3-lgpio liblgpio-dev || true
apt-cache show python3-gpiozero >/dev/null 2>&1 && apt-get install -y --no-install-recommends python3-gpiozero || true
apt-cache show nodejs >/dev/null 2>&1 && [ "$NO_WEB" -eq 0 ] && apt-get install -y --no-install-recommends nodejs npm || true

# ---------------------------------------------------------------------------
# User, directories, repository
# ---------------------------------------------------------------------------
step "user and directories"
id "$DAWN_USER" >/dev/null 2>&1 || useradd --system --create-home --home-dir /home/$DAWN_USER --shell /usr/sbin/nologin "$DAWN_USER"
for g in audio video render input gpio i2c dialout plugdev bluetooth netdev spi; do getent group "$g" >/dev/null && usermod -aG "$g" "$DAWN_USER"; done
DAWN_UID="$(id -u "$DAWN_USER")"
loginctl enable-linger "$DAWN_USER" || true

if [ "$HERE" != "$DAWN_DIR" ]; then
  mkdir -p "$DAWN_DIR"
  rsync -a --delete --exclude node_modules --exclude .venv --exclude var --exclude '__pycache__' "$HERE"/ "$DAWN_DIR"/
fi
chown -R "$DAWN_USER:$DAWN_USER" "$DAWN_DIR"
git config --system --add safe.directory "$DAWN_DIR" || true

# data dir (optionally on its own ext4 with data=journal)
mkdir -p /var/lib/dawn /etc/dawn
if [ -n "$DATA_DEVICE" ]; then
  blkid "$DATA_DEVICE" | grep -q 'TYPE="ext4"' || mkfs.ext4 -L dawn-data "$DATA_DEVICE"
  grep -q ' /var/lib/dawn ' /etc/fstab || echo "$DATA_DEVICE /var/lib/dawn ext4 defaults,noatime,data=journal 0 2" >>/etc/fstab
  mountpoint -q /var/lib/dawn || mount /var/lib/dawn
elif [ "$DATA_IMAGE_MB" -gt 0 ]; then
  IMG=/var/dawn-data.img
  [ -f "$IMG" ] || { fallocate -l "${DATA_IMAGE_MB}M" "$IMG"; mkfs.ext4 -F -L dawn-data "$IMG"; }
  grep -q ' /var/lib/dawn ' /etc/fstab || echo "$IMG /var/lib/dawn ext4 loop,defaults,noatime,data=journal 0 2" >>/etc/fstab
  mountpoint -q /var/lib/dawn || mount /var/lib/dawn
fi
mkdir -p /var/lib/dawn/media /var/lib/dawn/logos
chown -R "$DAWN_USER:$DAWN_USER" /var/lib/dawn
[ -f /etc/dawn/config.yaml ] || install -m 0644 "$DAWN_DIR/config/config.example.yaml" /etc/dawn/config.yaml
chown -R "$DAWN_USER:$DAWN_USER" /etc/dawn

# ---------------------------------------------------------------------------
# Python environment
# ---------------------------------------------------------------------------
step "python venv"
[ -d "$DAWN_DIR/.venv" ] || sudo -u "$DAWN_USER" python3 -m venv --system-site-packages "$DAWN_DIR/.venv"
sudo -u "$DAWN_USER" "$DAWN_DIR/.venv/bin/pip" install -q --upgrade pip
sudo -u "$DAWN_USER" "$DAWN_DIR/.venv/bin/pip" install -q -e "$DAWN_DIR/core[pi]" -e "$DAWN_DIR/dawn-timed" || \
  sudo -u "$DAWN_USER" "$DAWN_DIR/.venv/bin/pip" install -q -e "$DAWN_DIR/core" -e "$DAWN_DIR/dawn-timed"

# ---------------------------------------------------------------------------
# Web UI
# ---------------------------------------------------------------------------
step "web ui"
if [ "$NO_WEB" -eq 0 ]; then
  if command -v npm >/dev/null 2>&1; then
    if [ ! -f "$DAWN_DIR/web/dist/index.html" ] || [ -n "$(find "$DAWN_DIR/web/src" "$DAWN_DIR/web/face" -newer "$DAWN_DIR/web/dist/index.html" -print -quit 2>/dev/null)" ]; then
      (cd "$DAWN_DIR/web" && sudo -u "$DAWN_USER" npm ci --no-audit --no-fund && sudo -u "$DAWN_USER" npm run build)
    else
      echo "web/dist up to date"
    fi
  else
    [ -f "$DAWN_DIR/web/dist/index.html" ] || echo "WARNING: no npm and no web/dist; run 'make deploy' from a laptop to ship the built UI"
  fi
fi

# ---------------------------------------------------------------------------
# Source builds
# ---------------------------------------------------------------------------
build_rtlsdr() {
  if [ "$REBUILD" -eq 0 ] && command -v rtl_test >/dev/null 2>&1 && [ -f /usr/local/lib/librtlsdr.so ] ; then echo "rtl-sdr-blog present"; return; fi
  step "building rtl-sdr-blog (V4 support)"
  mkdir -p "$SRC_DIR"; cd "$SRC_DIR"
  [ -d rtl-sdr-blog ] || git clone --depth 1 "$RTLSDR_REPO" rtl-sdr-blog
  cd rtl-sdr-blog && git pull --ff-only || true
  mkdir -p build && cd build
  cmake .. -DINSTALL_UDEV_RULES=ON -DDETACH_KERNEL_DRIVER=ON -DCMAKE_INSTALL_PREFIX=/usr/local >/dev/null
  make -j"$(nproc)" >/dev/null && make install >/dev/null && ldconfig
  cat >/etc/modprobe.d/blacklist-rtlsdr.conf <<'BL'
# Dawn: keep the DVB kernel driver off the RTL-SDR stick
blacklist dvb_usb_rtl28xxu
blacklist rtl2832
blacklist rtl2830
blacklist rtl2832_sdr
BL
  echo "rtl-sdr-blog installed"
}
build_welle() {
  if [ "$REBUILD" -eq 0 ] && command -v welle-cli >/dev/null 2>&1; then echo "welle-cli present"; return; fi
  step "building welle.io (welle-cli)"
  mkdir -p "$SRC_DIR"; cd "$SRC_DIR"
  [ -d welle.io ] || git clone --depth 1 "$WELLE_REPO" welle.io
  cd welle.io && git pull --ff-only || true
  mkdir -p build && cd build
  cmake .. -DBUILD_WELLE_IO=OFF -DBUILD_WELLE_CLI=ON -DRTLSDR=ON -DAIRSPY=OFF -DSOAPYSDR=OFF -DPROFILING=OFF -DCMAKE_BUILD_TYPE=Release >/dev/null
  make -j"$( [ "$LOW_POWER" -eq 1 ] && echo 2 || nproc )" welle-cli >/dev/null
  install -m 0755 welle-cli /usr/local/bin/welle-cli
  echo "welle-cli installed: $(welle-cli --help 2>&1 | head -1 || true)"
}
build_shairport() {
  if [ "$REBUILD" -eq 0 ] && command -v nqptp >/dev/null 2>&1; then echo "nqptp present"; else
    step "building nqptp"; mkdir -p "$SRC_DIR"; cd "$SRC_DIR"
    [ -d nqptp ] || git clone --depth 1 "$NQPTP_REPO" nqptp
    cd nqptp && git pull --ff-only || true
    autoreconf -fi >/dev/null && ./configure --with-systemd-startup >/dev/null && make -j"$(nproc)" >/dev/null && make install >/dev/null
    systemctl enable nqptp >/dev/null 2>&1 || true
  fi
  if [ "$REBUILD" -eq 0 ] && command -v shairport-sync >/dev/null 2>&1 && shairport-sync -V 2>/dev/null | grep -q AirPlay2; then echo "shairport-sync (AirPlay 2) present"; return; fi
  step "building shairport-sync (AirPlay 2, PipeWire, metadata, D-Bus)"
  mkdir -p "$SRC_DIR"; cd "$SRC_DIR"
  [ -d shairport-sync ] || git clone --depth 1 "$SPS_REPO" shairport-sync
  cd shairport-sync && git pull --ff-only || true
  autoreconf -fi >/dev/null
  ./configure --sysconfdir=/etc --with-alsa --with-pw --with-avahi --with-ssl=openssl --with-soxr --with-metadata --with-dbus-interface --with-airplay-2 --with-systemd >/dev/null
  make -j"$(nproc)" >/dev/null && make install >/dev/null
  echo "shairport-sync installed: $(shairport-sync -V | head -1)"
}
if [ "$NO_BUILD" -eq 0 ]; then
  build_rtlsdr || echo "WARNING: rtl-sdr-blog build failed (DAB will not work until fixed)"
  build_welle || echo "WARNING: welle.io build failed (DAB will not work until fixed)"
  build_shairport || echo "WARNING: shairport-sync build failed (AirPlay unavailable)"
fi

# ---------------------------------------------------------------------------
# Boot config overlays (Pi only), marked block
# ---------------------------------------------------------------------------
if [ "$IS_PI" -eq 1 ] && [ -f "$BOOTCFG" ]; then
  step "boot config ($BOOTCFG)"
  # [all] first: the block is appended, so it must not land inside a [pi4]/[cm4] section
  OVERLAYS=$'[all]\ndtparam=i2c_arm=on\ndtparam=watchdog=on'
  case "$AUDIO" in
    # Pimoroni Audio Amp SHIM (MAX98357A): I2S on GPIO 18/19/21, GPIO25 driven high at boot.
    # Onboard audio off so the I2S card is the only audio device. Later lines win, so
    # this overrides the stock dtparam=audio=on further up the file.
    hifiberry) OVERLAYS+=$'\ndtparam=audio=off\ndtoverlay=hifiberry-dac\ngpio=25=op,dh';;
    *)         OVERLAYS+=$'\ndtparam=audio=on';;
  esac
  case "$PANEL_DETECTED" in
    # Waveshare 4.3" DSI 43H-800480-IPS-CT (thin panel, Goodix touch over the ribbon) presents
    # itself as the official 7" display. The older PCB-backed 4.3" LCD's overlay
    # (vc4-kms-dsi-waveshare-panel,4_3_inch) leaves this panel dark.
    waveshare_dsi) OVERLAYS+=$'\ndtoverlay=vc4-kms-dsi-7inch';;
    hyperpixel4)   OVERLAYS+=$'\ndtoverlay=vc4-kms-dpi-hyperpixel4';;
  esac
  [ "$LOW_POWER" -eq 1 ] && OVERLAYS+=$'\n# low-power board: smaller GPU split is fine for the kiosk\ngpu_mem=96'
  sed -i '/^# >>> dawn >>>/,/^# <<< dawn <<</d' "$BOOTCFG"
  printf '\n# >>> dawn >>>  (managed by deploy/install.sh; edit /etc/dawn/config.yaml instead)\n%s\n# <<< dawn <<<\n' "$OVERLAYS" >>"$BOOTCFG"
  grep -q '^dtoverlay=vc4-kms-v3d' "$BOOTCFG" || echo "WARNING: vc4-kms-v3d overlay not found in $BOOTCFG (needed for the DSI/DPI panel)"

  # Panel rotation (display.rotation in config.yaml): rotate the console/KMS output on the kernel
  # command line, and rotate touch to match with a libinput calibration matrix.
  CMDLINE=/boot/firmware/cmdline.txt; [ -f "$CMDLINE" ] || CMDLINE=/boot/cmdline.txt
  ROT="$(awk '/^display:/{d=1;next} /^[^ #]/{d=0} d && /^  rotation:/{print $2; exit}' /etc/dawn/config.yaml 2>/dev/null || true)"
  case "$ROT" in 90|180|270) ;; *) ROT=0;; esac
  case "$PANEL_DETECTED" in waveshare_dsi) CONN=DSI-1;; hyperpixel4) CONN=DPI-1;; *) CONN="";; esac
  if [ -f "$CMDLINE" ]; then
    sed -i -E 's/ ?video=(DSI|DPI)-1:[^ ]*//g' "$CMDLINE"
    [ "$ROT" != 0 ] && [ -n "$CONN" ] && sed -i -E "1s/\$/ video=$CONN:800x480M@60,rotate=$ROT/" "$CMDLINE"
  fi
  case "$ROT" in
    90)  MATRIX="0 -1 1 1 0 0";;
    180) MATRIX="-1 0 1 0 -1 1";;
    270) MATRIX="0 1 0 -1 0 1";;
    *)   MATRIX="";;
  esac
  if [ -n "$MATRIX" ]; then
    printf '# Dawn: touch follows display.rotation=%s (written by deploy/install.sh)\nENV{ID_INPUT_TOUCHSCREEN}=="1", ENV{LIBINPUT_CALIBRATION_MATRIX}="%s"\n' "$ROT" "$MATRIX" >/etc/udev/rules.d/98-dawn-touch.rules
  else
    rm -f /etc/udev/rules.d/98-dawn-touch.rules
  fi
  echo "panel rotation: $ROT"
fi

# ---------------------------------------------------------------------------
# System configuration files
# ---------------------------------------------------------------------------
step "system configuration"
D="$DAWN_DIR/deploy"
install -m 0644 "$D/udev/99-dawn.rules" /etc/udev/rules.d/99-dawn.rules
[ -f /etc/chrony/chrony.conf.dawn-orig ] || cp /etc/chrony/chrony.conf /etc/chrony/chrony.conf.dawn-orig 2>/dev/null || true
install -m 0644 "$D/chrony/chrony.conf" /etc/chrony/chrony.conf
install -m 0644 "$D/gpsd/gpsd" /etc/default/gpsd
if [ "$AUDIO" = hifiberry ]; then EQ_CONF=dawn-eq-mono.conf; else EQ_CONF=dawn-eq.conf; fi
install -D -m 0644 "$D/pipewire/$EQ_CONF" /etc/pipewire/pipewire.conf.d/dawn-eq.conf
install -D -m 0644 "$D/wireplumber/51-dawn-bluetooth.conf" /etc/wireplumber/wireplumber.conf.d/51-dawn-bluetooth.conf
install -D -m 0644 "$D/wireplumber/52-dawn-alsa.conf" /etc/wireplumber/wireplumber.conf.d/52-dawn-alsa.conf
# The I2S amp is the only speaker: pin it so a USB audio device plugged in later (usb ranks first
# in audio.sink_priority) cannot take over. Only replaces an unset pin; a user's choice stays.
[ "$AUDIO" = hifiberry ] && sed -i -E 's/^(\s*pinned_sink:\s*)null\s*$/\1hifiberry/' /etc/dawn/config.yaml || true
[ -f /etc/shairport-sync.conf ] && [ ! -f /etc/shairport-sync.conf.dawn-orig ] && cp /etc/shairport-sync.conf /etc/shairport-sync.conf.dawn-orig || true
install -m 0644 "$D/shairport-sync/shairport-sync.conf" /etc/shairport-sync.conf
install -m 0644 "$D/logrotate/dawn" /etc/logrotate.d/dawn
install -D -m 0644 "$D/polkit/50-dawn.rules" /etc/polkit-1/rules.d/50-dawn.rules
install -D -m 0644 "$D/avahi/dawn.service" /etc/avahi/services/dawn.service
install -D -m 0644 "$D/systemd/system.conf.d/dawn-watchdog.conf" /etc/systemd/system.conf.d/dawn-watchdog.conf
install -m 0755 "$D/bin/dawn-face" /usr/local/bin/dawn-face
install -m 0755 "$D/bin/dawn-update" /usr/local/bin/dawn-update
install -m 0755 "$D/bin/dawn-airplay-name" /usr/local/bin/dawn-airplay-name
install -m 0755 "$D/bin/dawn-set-hostname" /usr/local/bin/dawn-set-hostname
install -m 0440 "$D/sudoers/dawn" /etc/sudoers.d/dawn && visudo -cf /etc/sudoers.d/dawn >/dev/null
sed -i -E 's/^(\s*name\s*=\s*)"[^"]*"/\1"'"$(grep -E '^\s*name:' /etc/dawn/config.yaml | head -1 | sed -E 's/.*name:\s*//; s/^"//; s/"$//' || echo Dawn)"'"/' /etc/shairport-sync.conf || true
udevadm control --reload-rules && udevadm trigger || true

# chrony SHM units: gpsd creates 0 and 1; make sure unit 2 (dawn-timed) can be created unprivileged
echo 'kernel.shmmax = 268435456' >/etc/sysctl.d/90-dawn.conf; sysctl -q --system || true

# ---------------------------------------------------------------------------
# systemd units (uid substituted)
# ---------------------------------------------------------------------------
step "systemd units"
for u in dawn-core dawn-dab dawn-timed dawn-face; do
  sed "s|@UID@|$DAWN_UID|g; s|@DAWN_DIR@|$DAWN_DIR|g" "$D/systemd/$u.service" >"/etc/systemd/system/$u.service"
done
mkdir -p /etc/systemd/system/shairport-sync.service.d
sed "s|@UID@|$DAWN_UID|g" "$D/systemd/shairport-sync.service.d/dawn.conf" >/etc/systemd/system/shairport-sync.service.d/dawn.conf
systemctl --global enable pipewire.socket pipewire-pulse.socket wireplumber.service >/dev/null 2>&1 || true
systemctl daemon-reload
systemctl disable --now getty@tty1.service >/dev/null 2>&1 || true
systemctl enable NetworkManager avahi-daemon bluetooth chrony gpsd >/dev/null 2>&1 || true
command -v nqptp >/dev/null 2>&1 && systemctl enable nqptp >/dev/null 2>&1 || true
command -v shairport-sync >/dev/null 2>&1 && systemctl enable shairport-sync >/dev/null 2>&1 || true
systemctl enable dawn-core dawn-dab dawn-timed dawn-face >/dev/null
systemctl restart chrony gpsd avahi-daemon >/dev/null 2>&1 || true
systemctl restart dawn-core dawn-dab dawn-timed || true
systemctl restart dawn-face >/dev/null 2>&1 || true
command -v shairport-sync >/dev/null 2>&1 && systemctl restart nqptp shairport-sync >/dev/null 2>&1 || true

# hostname from config
WANT_HOST="$(grep -E '^\s*hostname:' /etc/dawn/config.yaml | head -1 | sed -E 's/.*hostname:\s*//; s/"//g' || true)"
if [ -n "$WANT_HOST" ] && [ "$WANT_HOST" != "$(hostname)" ]; then /usr/local/bin/dawn-set-hostname "$WANT_HOST" || true; fi

# ---------------------------------------------------------------------------
# Optional read-only root
# ---------------------------------------------------------------------------
if [ "$READONLY" -eq 1 ]; then
  step "read-only root (overlayfs)"
  if [ -z "$DATA_DEVICE" ]; then echo "WARNING: --readonly without --data-device: /var/lib/dawn would be lost on reboot; skipping"; else
    command -v raspi-config >/dev/null && raspi-config nonint enable_overlayfs && echo "overlayfs enabled: reboot to activate (disable with 'raspi-config nonint disable_overlayfs')"
  fi
fi

echo
echo "=== Dawn installed. Face: http://$(hostname).local/face  Control UI: http://$(hostname).local/ ==="
echo "Logs: journalctl -u dawn-core -u dawn-dab -u dawn-timed -u dawn-face -f"
[ "$UPDATE" -eq 0 ] && [ "$IS_PI" -eq 1 ] && echo "A reboot is recommended after the first install (overlays, groups, watchdog)."
exit 0
