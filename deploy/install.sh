#!/usr/bin/env bash
# Dawn installer for Raspberry Pi OS (Bookworm / Trixie, 64-bit or 32-bit). Idempotent.
#
#   sudo ./deploy/install.sh                 full install (builds rtl-sdr-blog, welle.io, shairport-sync + nqptp)
#   sudo ./deploy/install.sh --update        after `git pull`: reinstall python/web/configs, skip finished builds
#   sudo ./deploy/install.sh --audio shim|usb|headphones|hdmi|auto   speaker output (default: shim):
#       shim (or hifiberry)  Pimoroni Audio Amp SHIM / HiFiBerry I2S amp: hifiberry-dac overlay, onboard audio off,
#                            mono EQ chain, output pinned to the amp
#       usb | headphones (or jack) | hdmi   onboard audio on, stereo EQ chain, output pinned to that kind
#       auto                 onboard audio on, no pin: audio.sink_priority picks at boot
#     The choice is remembered (/etc/dawn/audio-output), so a later run without --audio keeps it.
#   sudo ./deploy/install.sh --display hyperpixel4|waveshare_dsi|hdmi   force the panel overlay (default: auto)
#   sudo ./deploy/install.sh --data-device /dev/mmcblk0p3   mount /var/lib/dawn from an ext4 partition (data=journal)
#   sudo ./deploy/install.sh --data-image-mb 1024          ...or from a loop-mounted ext4 image (data=journal)
#   sudo ./deploy/install.sh --readonly      enable the overlayfs read-only root (needs --data-device)
#   sudo ./deploy/install.sh --no-build      skip source builds (use whatever is installed)
#   sudo ./deploy/install.sh --rebuild       force rebuilding rtl-sdr / welle / shairport-sync
#   sudo ./deploy/install.sh --no-web        skip the web build (e.g. when `make deploy` ships web/dist)
#   sudo ./deploy/install.sh --no-restart    install everything but restart nothing (dawn-update restarts in order)
#
# Everything it writes is marked or in its own file, so re-running is safe.
set -euo pipefail

DAWN_DIR=/opt/dawn
DAWN_USER=dawn
LOG=/var/log/dawn-install.log
SRC_DIR=/usr/local/src
UPDATE=0 REBUILD=0 NO_BUILD=0 NO_WEB=0 READONLY=0 NO_RESTART=0
DATA_DEVICE="" DATA_IMAGE_MB=0 AUDIO="" PANEL=auto
RTLSDR_REPO=https://github.com/rtlsdrblog/rtl-sdr-blog.git
WELLE_REPO=https://github.com/AlbrechtL/welle.io.git
# known-good releases instead of a moving master (override in the environment to try another)
RTLSDR_REF="${RTLSDR_REF:-v1.3.6}"
WELLE_REF="${WELLE_REF:-v2.7}"
VERSIONS_JSON=/usr/local/share/dawn/versions.json  # what was built, shown under Diagnostics -> DAB radio
SDR_LIB_WARNING=/var/lib/dawn/sdr-lib-warning
SPS_REPO=https://github.com/mikebrady/shairport-sync.git
NQPTP_REPO=https://github.com/mikebrady/nqptp.git
DAWN_REPO="${DAWN_REPO:-https://github.com/tunlezah/dawn.git}"  # where Settings → Software update pulls from
# npm/pip caches: dawn's home is read-only during an update from the UI (dawn-core has ProtectHome=read-only)
NPM_CACHE=/var/cache/dawn/npm PIP_CACHE=/var/cache/dawn/pip

while [ $# -gt 0 ]; do
  case "$1" in
    --update) UPDATE=1;;
    --rebuild) REBUILD=1;;
    --no-build) NO_BUILD=1;;
    --no-web) NO_WEB=1;;
    --no-restart) NO_RESTART=1;;
    --readonly) READONLY=1;;
    --audio) AUDIO="$2"; shift;;
    --display) PANEL="$2"; shift;;
    --data-device) DATA_DEVICE="$2"; shift;;
    --data-image-mb) DATA_IMAGE_MB="$2"; shift;;
    -h|--help) sed -n '2,25p' "$0"; exit 0;;
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
# Speaker output: --audio, else what an earlier install chose, else the Audio Amp SHIM (reference build).
AUDIO_FILE=/etc/dawn/audio-output
detect_audio() {
  local a="$AUDIO"
  [ -n "$a" ] || a="$(cat "$AUDIO_FILE" 2>/dev/null || true)"
  [ -n "$a" ] || a=hifiberry
  case "$a" in
    shim|amp-shim|hifiberry) echo hifiberry;;
    jack|headphones) echo headphones;;
    usb|hdmi|auto) echo "$a";;
    *) echo "unknown --audio $a (shim, usb, headphones, hdmi or auto)" >&2; exit 2;;
  esac
}
AUDIO="$(detect_audio)"
echo "model: $MODEL ($ARCH, $CODENAME) low_power=$LOW_POWER panel=$PANEL_DETECTED audio=$AUDIO"

# ---------------------------------------------------------------------------
# Packages
# ---------------------------------------------------------------------------
step "apt packages"
export DEBIAN_FRONTEND=noninteractive
CHROMIUM=chromium; apt-cache show chromium >/dev/null 2>&1 || CHROMIUM=chromium-browser
PKGS=(
  git curl rsync build-essential cmake pkg-config autoconf automake libtool xxd
  python3 python3-venv python3-dev python3-pip python3-systemd python3-libgpiod
  libusb-1.0-0-dev libfaad-dev libmpg123-dev libmp3lame-dev libfftw3-dev libasound2-dev
  libpopt-dev libconfig-dev libavahi-client-dev libssl-dev libsoxr-dev libplist-dev libsodium-dev
  libavutil-dev libavcodec-dev libavformat-dev uuid-dev libgcrypt-dev libpipewire-0.3-dev libglib2.0-dev libplist-utils libswresample-dev systemd-dev
  mpv pipewire pipewire-pulse pipewire-audio wireplumber libspa-0.2-bluetooth alsa-utils
  bluez gpsd gpsd-clients chrony avahi-daemon network-manager iw i2c-tools
  cage "$CHROMIUM" fonts-dejavu-core logrotate
)
# An update skips apt when the package list is the one installed last time: apt needs the network, and failing there
# after dawn-update has moved the code would only end in a rollback for nothing
APT_STAMP=/var/lib/dawn/apt-pkgs.sha256
PKGS_HASH="$(printf '%s\n' "${PKGS[@]}" "web=$NO_WEB" | sha256sum | cut -d' ' -f1)"
if [ "$UPDATE" -eq 1 ] && [ -f "$APT_STAMP" ] && [ "$(cat "$APT_STAMP" 2>/dev/null)" = "$PKGS_HASH" ]; then
  echo "apt packages unchanged since the last install; skipping apt"
else
  apt-get update -qq
  apt-get install -y --no-install-recommends "${PKGS[@]}" || apt-get install -y --no-install-recommends "${PKGS[@]/libpipewire-0.3-dev/}"
  apt-cache show python3-lgpio >/dev/null 2>&1 && apt-get install -y --no-install-recommends python3-lgpio liblgpio-dev || true
  apt-cache show python3-gpiozero >/dev/null 2>&1 && apt-get install -y --no-install-recommends python3-gpiozero || true
  apt-cache show nodejs >/dev/null 2>&1 && [ "$NO_WEB" -eq 0 ] && apt-get install -y --no-install-recommends nodejs npm || true
  mkdir -p "$(dirname "$APT_STAMP")" && echo "$PKGS_HASH" >"$APT_STAMP"
fi

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
  # a source without .git (a copied folder) must not delete the git checkout already in $DAWN_DIR
  GIT_EXCL=(); [ -d "$HERE/.git" ] || GIT_EXCL=(--exclude .git)
  rsync -a --delete --exclude node_modules --exclude .venv --exclude var --exclude '__pycache__' "${GIT_EXCL[@]}" "$HERE"/ "$DAWN_DIR"/
fi
chown -R "$DAWN_USER:$DAWN_USER" "$DAWN_DIR"
git config --system --add safe.directory "$DAWN_DIR" || true
# Software update (dawn-update) fetches from origin: make sure there is one
if [ -d "$DAWN_DIR/.git" ]; then
  git -C "$DAWN_DIR" remote get-url origin >/dev/null 2>&1 || { sudo -u "$DAWN_USER" git -C "$DAWN_DIR" remote add origin "$DAWN_REPO"; echo "git remote origin -> $DAWN_REPO"; }
else
  echo "WARNING: $DAWN_DIR is not a git checkout, so Software update cannot work; install from a clone of $DAWN_REPO"
fi
mkdir -p "$NPM_CACHE" "$PIP_CACHE"
chown -R "$DAWN_USER:$DAWN_USER" /var/cache/dawn

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
PIP=(sudo -u "$DAWN_USER" env PIP_CACHE_DIR="$PIP_CACHE" "$DAWN_DIR/.venv/bin/pip")
"${PIP[@]}" install -q --upgrade pip
"${PIP[@]}" install -q -e "$DAWN_DIR/core[pi]" -e "$DAWN_DIR/dawn-timed" || \
  "${PIP[@]}" install -q -e "$DAWN_DIR/core" -e "$DAWN_DIR/dawn-timed"

# ---------------------------------------------------------------------------
# Web UI
# ---------------------------------------------------------------------------
step "web ui"
if [ "$NO_WEB" -eq 0 ]; then
  if command -v npm >/dev/null 2>&1; then
    if [ ! -f "$DAWN_DIR/web/dist/index.html" ] || [ -n "$(find "$DAWN_DIR/web/src" "$DAWN_DIR/web/face" -newer "$DAWN_DIR/web/dist/index.html" -print -quit 2>/dev/null)" ]; then
      NPM=(sudo -u "$DAWN_USER" env npm_config_cache="$NPM_CACHE" npm)
      # into web/dist.new, swapped in only when complete: a failed build never leaves the face without a UI
      "$DAWN_DIR/deploy/build-web.sh" "$DAWN_DIR/web" "${NPM[@]}" || { echo "ERROR: the web UI did not build; web/dist is unchanged"; exit 1; }
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
# checkout <dir> <repo> <ref>: a shallow clone of exactly that tag or commit
checkout() {
  local dir="$1" repo="$2" ref="$3"
  if [ ! -d "$dir/.git" ]; then
    rm -rf "$dir"
    git clone --depth 1 --branch "$ref" "$repo" "$dir" 2>/dev/null || git clone "$repo" "$dir" || return 1
  fi
  ( cd "$dir" && { git fetch --depth 1 origin "$ref" 2>/dev/null && git checkout -q --detach FETCH_HEAD; } || git checkout -q --detach "$ref" ) || return 1
}
built_ref() {  # built_ref <name>: the ref recorded at the last build ("" when unknown)
  python3 - "$VERSIONS_JSON" "$1" <<'PY' 2>/dev/null || true
import json, sys
try:
    print(json.load(open(sys.argv[1])).get(sys.argv[2], {}).get("ref", ""))
except Exception:
    print("")
PY
}
record_build() {  # record_build <name> <ref> <src dir>
  mkdir -p "$(dirname "$VERSIONS_JSON")"
  python3 - "$VERSIONS_JSON" "$1" "$2" "$(git -C "$3" rev-parse --short HEAD 2>/dev/null || echo '?')" <<'PY' || true
import json, sys, time
path, name, ref, commit = sys.argv[1:5]
try:
    data = json.load(open(path))
except Exception:
    data = {}
data[name] = {"ref": ref, "commit": commit, "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
json.dump(data, open(path, "w"), indent=1)
PY
}
# a rebuild is due with --rebuild, when the binary is missing, or when a different ref than the one built is wanted
# (an install from before refs were recorded keeps what it has; the link check below still runs)
want_build() {  # want_build <name> <ref> <present?>
  [ "$REBUILD" -eq 1 ] && return 0
  [ "$3" -eq 0 ] && return 0
  local was; was="$(built_ref "$1")"
  [ -n "$was" ] && [ "$was" != "$2" ]
}
RTLSDR_BUILT=0
build_rtlsdr() {
  local present=0
  command -v rtl_test >/dev/null 2>&1 && [ -f /usr/local/lib/librtlsdr.so ] && present=1
  if ! want_build rtlsdr "$RTLSDR_REF" "$present"; then echo "rtl-sdr-blog present"; return 0; fi
  step "building rtl-sdr-blog $RTLSDR_REF (V4 support)"
  mkdir -p "$SRC_DIR"; cd "$SRC_DIR"
  checkout rtl-sdr-blog "$RTLSDR_REPO" "$RTLSDR_REF" || return 1
  mkdir -p rtl-sdr-blog/build && cd rtl-sdr-blog/build
  # each step returns on failure: called as `build_rtlsdr || echo WARNING`, set -e does not apply inside it
  cmake .. -DINSTALL_UDEV_RULES=ON -DDETACH_KERNEL_DRIVER=ON -DCMAKE_INSTALL_PREFIX=/usr/local >/dev/null || return 1
  make -j"$(nproc)" >/dev/null || return 1
  make install >/dev/null || return 1
  ldconfig
  record_build rtlsdr "$RTLSDR_REF" "$SRC_DIR/rtl-sdr-blog"
  RTLSDR_BUILT=1
  echo "rtl-sdr-blog $RTLSDR_REF installed"
}
build_welle() {
  local present=0 force="${1:-0}"
  command -v welle-cli >/dev/null 2>&1 && present=1
  # a welle-cli built before the blog library was installed links the distro librtlsdr and never gets V4 support
  if [ "$force" -eq 0 ] && [ "$RTLSDR_BUILT" -eq 0 ] && ! want_build welle "$WELLE_REF" "$present"; then echo "welle-cli present"; return 0; fi
  step "building welle.io $WELLE_REF (welle-cli)"
  mkdir -p "$SRC_DIR"; cd "$SRC_DIR"
  checkout welle.io "$WELLE_REPO" "$WELLE_REF" || return 1
  rm -rf welle.io/build && mkdir -p welle.io/build && cd welle.io/build
  cmake .. -DBUILD_WELLE_IO=OFF -DBUILD_WELLE_CLI=ON -DRTLSDR=ON -DAIRSPY=OFF -DSOAPYSDR=OFF -DPROFILING=OFF -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_PREFIX_PATH=/usr/local >/dev/null || return 1
  make -j"$( [ "$LOW_POWER" -eq 1 ] && echo 2 || nproc )" welle-cli >/dev/null || return 1
  install -m 0755 welle-cli /usr/local/bin/welle-cli || return 1
  record_build welle "$WELLE_REF" "$SRC_DIR/welle.io"
  echo "welle-cli installed: $(welle-cli --help 2>&1 | head -1 || true)"
}
welle_links_blog_lib() { ldd /usr/local/bin/welle-cli 2>/dev/null | grep -q 'librtlsdr.* => /usr/local/lib/'; }
check_welle_link() {
  command -v welle-cli >/dev/null 2>&1 || return 0
  [ -f /usr/local/lib/librtlsdr.so ] || return 0
  if welle_links_blog_lib; then rm -f "$SDR_LIB_WARNING"; return 0; fi
  echo "welle-cli does not use /usr/local/lib/librtlsdr (the RTL-SDR Blog library with V4 support); rebuilding it once"
  build_welle 1 || true
  if welle_links_blog_lib; then rm -f "$SDR_LIB_WARNING"; return 0; fi
  local got; got="$(ldd /usr/local/bin/welle-cli 2>/dev/null | grep librtlsdr | xargs || true)"
  echo "WARNING: welle-cli still links '${got:-no librtlsdr}', not /usr/local/lib: an RTL-SDR Blog V4 will not tune. Re-run with --rebuild."
  mkdir -p "$(dirname "$SDR_LIB_WARNING")"
  echo "welle-cli links ${got:-no librtlsdr} instead of the RTL-SDR Blog library in /usr/local/lib" >"$SDR_LIB_WARNING"
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
  ./configure --sysconfdir=/etc --with-alsa --with-pipewire --with-avahi --with-ssl=openssl --with-soxr --with-metadata --with-dbus-interface --with-airplay-2 --with-systemd-startup >/dev/null || return 1
  make -j"$(nproc)" >/dev/null && make install >/dev/null || return 1
  echo "shairport-sync installed: $(shairport-sync -V | head -1)"
}
# The DVB-T kernel driver must not claim the stick: written on every run (it used to be written only when the library
# was built, so an install that found librtlsdr already there never got it), and unloaded now if it is loaded
cat >/etc/modprobe.d/blacklist-rtlsdr.conf <<'BL'
# Dawn: keep the DVB kernel driver off the RTL-SDR stick
blacklist dvb_usb_rtl28xxu
blacklist rtl2832
blacklist rtl2830
blacklist rtl2832_sdr
blacklist r820t
BL
modprobe -r dvb_usb_rtl28xxu rtl2832_sdr rtl2832 r820t 2>/dev/null || true

if [ "$NO_BUILD" -eq 0 ]; then
  build_rtlsdr || echo "WARNING: rtl-sdr-blog build failed (DAB will not work until fixed)"
  build_welle || echo "WARNING: welle.io build failed (DAB will not work until fixed)"
  check_welle_link
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
# Pin the chosen output so a USB audio device plugged in later (usb ranks first in audio.sink_priority)
# cannot take over the speaker; auto unpins. Only an unset pin or a sink kind is replaced: a specific
# PipeWire node picked in the web UI stays.
[ "$AUDIO" = auto ] && PIN=null || PIN="$AUDIO"
sed -i -E 's/^(\s*pinned_sink:\s*)(null|usb|hifiberry|headphones|hdmi)?\s*$/\1'"$PIN"'/' /etc/dawn/config.yaml || true
echo "$AUDIO" >"$AUDIO_FILE"
[ -f /etc/shairport-sync.conf ] && [ ! -f /etc/shairport-sync.conf.dawn-orig ] && cp /etc/shairport-sync.conf /etc/shairport-sync.conf.dawn-orig || true
install -m 0644 "$D/shairport-sync/shairport-sync.conf" /etc/shairport-sync.conf
install -D -m 0644 "$D/dbus/dawn-shairport-sync.conf" /etc/dbus-1/system.d/dawn-shairport-sync.conf
install -m 0644 "$D/logrotate/dawn" /etc/logrotate.d/dawn
install -D -m 0644 "$D/polkit/50-dawn.rules" /etc/polkit-1/rules.d/50-dawn.rules
install -D -m 0644 "$D/avahi/dawn.service" /etc/avahi/services/dawn.service
install -D -m 0644 "$D/systemd/system.conf.d/dawn-watchdog.conf" /etc/systemd/system.conf.d/dawn-watchdog.conf
install -m 0755 "$D/bin/dawn-face" /usr/local/bin/dawn-face
install -m 0755 "$D/bin/dawn-update" /usr/local/bin/dawn-update
install -m 0755 "$D/bin/dawn-airplay-name" /usr/local/bin/dawn-airplay-name
install -m 0755 "$D/bin/dawn-set-hostname" /usr/local/bin/dawn-set-hostname
install -m 0755 "$D/bin/dawn-usb-reset" /usr/local/bin/dawn-usb-reset
# the update log: written by dawn-update.service (root), read by dawn-core for the UI
[ -f /var/log/dawn-update.log ] || install -m 0644 /dev/null /var/log/dawn-update.log
install -m 0440 "$D/sudoers/dawn" /etc/sudoers.d/dawn && visudo -cf /etc/sudoers.d/dawn >/dev/null
sed -i -E 's/^(\s*name\s*=\s*)"[^"]*"/\1"'"$(grep -E '^\s*name:' /etc/dawn/config.yaml | head -1 | sed -E 's/.*name:\s*//; s/^"//; s/"$//' || echo Dawn)"'"/' /etc/shairport-sync.conf || true
udevadm control --reload-rules && udevadm trigger || true

# chrony SHM units: gpsd creates 0 and 1; make sure unit 2 (dawn-timed) can be created unprivileged
echo 'kernel.shmmax = 268435456' >/etc/sysctl.d/90-dawn.conf; sysctl -q --system || true

# dtparam=i2c_arm=on only enables the bus; the light sensor is read through /dev/i2c-1, which needs i2c-dev
echo i2c-dev >/etc/modules-load.d/dawn-i2c.conf; modprobe i2c-dev || true

# ---------------------------------------------------------------------------
# systemd units (uid substituted)
# ---------------------------------------------------------------------------
step "systemd units"
for u in dawn-core dawn-dab dawn-timed dawn-face dawn-update; do
  sed "s|@UID@|$DAWN_UID|g; s|@DAWN_DIR@|$DAWN_DIR|g" "$D/systemd/$u.service" >"/etc/systemd/system/$u.service"
done
mkdir -p /etc/systemd/system/shairport-sync.service.d
sed "s|@UID@|$DAWN_UID|g" "$D/systemd/shairport-sync.service.d/dawn.conf" >/etc/systemd/system/shairport-sync.service.d/dawn.conf
for u in chrony gpsd; do
  install -D -m 0644 "$D/systemd/$u.service.d/dawn.conf" "/etc/systemd/system/$u.service.d/dawn.conf"
done
# cool mode: dawn-core may write the CPU frequency cap (group dawn)
install -D -m 0644 "$D/tmpfiles.d/dawn-cpufreq.conf" /etc/tmpfiles.d/dawn-cpufreq.conf
systemd-tmpfiles --create /etc/tmpfiles.d/dawn-cpufreq.conf 2>/dev/null || true
systemctl --global enable pipewire.socket pipewire-pulse.socket wireplumber.service >/dev/null 2>&1 || true
systemctl daemon-reload
systemctl disable --now getty@tty1.service >/dev/null 2>&1 || true
systemctl enable NetworkManager avahi-daemon bluetooth chrony gpsd >/dev/null 2>&1 || true
# Raspberry Pi OS ships with Bluetooth rfkill soft-blocked; systemd-rfkill keeps the unblock across reboots
rfkill unblock bluetooth 2>/dev/null || true
command -v nqptp >/dev/null 2>&1 && systemctl enable nqptp >/dev/null 2>&1 || true
command -v shairport-sync >/dev/null 2>&1 && systemctl enable shairport-sync >/dev/null 2>&1 || true
systemctl enable dawn-core dawn-dab dawn-timed >/dev/null
# reenable: moves an older install's graphical.target symlink to multi-user.target
systemctl reenable dawn-face >/dev/null 2>&1
if [ "$NO_RESTART" -eq 0 ]; then
  systemctl restart chrony gpsd avahi-daemon >/dev/null 2>&1 || true
  systemctl restart dawn-core dawn-dab dawn-timed || true
  systemctl restart dawn-face >/dev/null 2>&1 || true
  command -v shairport-sync >/dev/null 2>&1 && systemctl restart nqptp shairport-sync >/dev/null 2>&1 || true
else
  # dawn-update restarts dawn-timed and dawn-dab, then dawn-core (checking it is healthy), then the face; the
  # supporting daemons pick up their new config here (none of them carries the alarms)
  systemctl try-restart chrony gpsd avahi-daemon >/dev/null 2>&1 || true
  command -v shairport-sync >/dev/null 2>&1 && systemctl try-restart nqptp shairport-sync >/dev/null 2>&1 || true
fi

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
echo "=== Dawn installed. Face: http://$(hostname).local:8080/face  Control UI: http://$(hostname).local:8080/ ==="
echo "Logs: journalctl -u dawn-core -u dawn-dab -u dawn-timed -u dawn-face -f"
[ "$UPDATE" -eq 0 ] && [ "$IS_PI" -eq 1 ] && echo "A reboot is recommended after the first install (overlays, groups, watchdog)."
exit 0
