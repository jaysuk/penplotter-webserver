#!/bin/bash

dir="$HOME/webplotter"
venv="$HOME/penplotter_venv"
# The application runs unmodified on every supported Python version, so one branch serves all OS releases
git="${WEBPLOTTER_REPO:-https://github.com/jaysuk/penplotter-webserver.git}"
BRANCH="${WEBPLOTTER_BRANCH:-PiPlot}"

#######################################################
#######################################################

# Colours and cursor control only when talking to a terminal (the CI run is not one)
if [ -t 1 ]; then
    BOLD=$'\e[1m'; DIM=$'\e[2m'; RED=$'\e[31m'; GREEN=$'\e[32m'; YELLOW=$'\e[33m'; CYAN=$'\e[36m'; RESET=$'\e[0m'
    ISTTY=1
else
    BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; CYAN=""; RESET=""
    ISTTY=0
fi

# Everything the installed tools print goes in here, and is shown if a step fails
LOG="${WEBPLOTTER_LOG:-$HOME/webplotter-install.log}"
: > "$LOG" 2>/dev/null || LOG=/dev/null

SUDO_KEEPALIVE=""
STEP=0
TOTAL=0

# Always give the cursor back and stop the sudo keepalive, however the script ends
cleanup()
{
    [ -n "$SUDO_KEEPALIVE" ] && kill "$SUDO_KEEPALIVE" 2>/dev/null
    [ "$ISTTY" -eq 1 ] && printf '\033[?25h'
}
trap cleanup EXIT
trap 'echo ""; echo "Interrupted."; exit 130' INT TERM

die()
{
    echo "${BOLD}${RED} $1${RESET}" >&2
    [ "$LOG" != /dev/null ] && echo "${DIM} Full log: $LOG${RESET}" >&2
    exit 1
}

banner()
{
    printf '%s' "$CYAN"
    cat <<'EOF'

 ____  _ ____  _       _
|  _ \(_)  _ \| | ___ | |_
| |_) | | |_) | |/ _ \| __|
|  __/| |  __/| | (_) | |_
|_|   |_|_|   |_|\___/ \__|
EOF
    printf '%s' "$RESET"
    echo "${DIM} ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ o${RESET}"
    echo "${BOLD} Pen plotter web server installer${RESET}"
    echo ""
    echo " This can take 10-20 minutes on a Raspberry Pi Zero. While a spinner and"
    echo " a timer are moving, it is working and has not got stuck."
    echo " Details are written to ${BOLD}$LOG${RESET}"
}

# Print a numbered heading for the next stage
step()
{
    STEP=$((STEP + 1))
    echo ""
    echo "${BOLD}${CYAN}[$STEP/$TOTAL]${RESET}${BOLD} $1${RESET}"
}

ok()   { echo "  ${GREEN}[ ok ]${RESET} $1"; }
warn() { echo "  ${YELLOW}[warn]${RESET} $1"; }
note() { echo "  $1"; }

fmt_time()
{
    if [ "$1" -ge 60 ]; then
        printf '%dm%02ds' $(($1 / 60)) $(($1 % 60))
    else
        printf '%ds' "$1"
    fi
}

# Draw a spinner with the elapsed time while a background process runs
# $1: pid  $2: label  $3: $SECONDS at start
spinner()
{
    local pid=$1 label=$2 start=$3
    local frames='|/-\' i=0 secs hint
    while kill -0 "$pid" 2>/dev/null; do
        secs=$((SECONDS - start))
        hint=""
        [ "$secs" -ge 90 ] && hint=" - still working, this is normal"
        printf '\r\033[K  %s%s%s %s %s(%s%s)%s' "$CYAN" "${frames:i%4:1}" "$RESET" "$label" "$DIM" "$(fmt_time "$secs")" "$hint" "$RESET"
        i=$((i + 1))
        sleep 0.2
    done
    printf '\r\033[K'
}

# Run a command in the background with a spinner, logging its output.
# Returns the exit status of the command itself; on failure the end of the log is shown.
run_spin()
{
    local label=$1
    shift
    local start=$SECONDS rc
    printf '\n=== %s\n$ %s\n' "$label" "$*" >> "$LOG"
    "$@" >> "$LOG" 2>&1 < /dev/null &
    local pid=$!
    if [ "$ISTTY" -eq 1 ]; then
        spinner "$pid" "$label" "$start"
    else
        note "$label ..."
    fi
    wait "$pid"
    rc=$?
    if [ "$rc" -eq 0 ]; then
        echo "  ${GREEN}[ ok ]${RESET} $label ${DIM}($(fmt_time $((SECONDS - start))))${RESET}"
    else
        echo "  ${RED}[fail]${RESET} $label ${DIM}($(fmt_time $((SECONDS - start))))${RESET}"
        if [ "$LOG" != /dev/null ]; then
            echo "${DIM}  --- end of $LOG ---"
            tail -n 15 "$LOG" | sed 's/^/  | /'
            echo "  ---${RESET}"
        fi
    fi
    return "$rc"
}

# Ask for the sudo password now, in the foreground where it can be seen, and keep it
# fresh for the rest of the install. A password prompt hidden behind a spinner looks
# exactly like a hang.
ensure_sudo()
{
    if ! sudo -n true 2>/dev/null; then
        note "Some steps need administrator rights."
        note "${BOLD}Please type your password now${RESET} (nothing is shown as you type):"
        sudo -v || die "Could not get administrator rights. Run this from a terminal, as a user that can use sudo."
    fi
    ok "Administrator rights"
    ( while kill -0 "$$" 2>/dev/null; do sudo -n -v 2>/dev/null; sleep 50; done ) &
    SUDO_KEEPALIVE=$!
    [ "$ISTTY" -eq 1 ] && printf '\033[?25l'
}

banner

# The service file is written for the user running this script and $HOME, so the two must agree.
# "sudo bash install.sh" breaks that (root, with the real user's home), and the script uses sudo itself.
if [ -n "$SUDO_USER" ] && [ "$(id -u)" -eq 0 ]; then
    die "Do not run this installer with sudo. Run it as your normal user ($SUDO_USER); it asks for sudo when it needs it."
fi
if [ "$(stat -c %U "$HOME" 2>/dev/null)" != "$(id -un)" ]; then
    die "Your home folder $HOME does not belong to $(id -un), so the service would be set up wrongly. Log in as the user that should run the web plotter and try again."
fi
if [ "$(id -u)" -eq 0 ]; then
    echo ""
    echo "${YELLOW} Warning: running as root, so the web plotter service will run as root too.${RESET}"
    echo "${YELLOW} A normal user is recommended; the default Pi user needs no extra setup.${RESET}"
fi

#System Info
. /etc/os-release 2>/dev/null
echo ""
echo "${BOLD} System${RESET}"
note "${PRETTY_NAME:-unknown OS}, $(getconf LONG_BIT)-bit"

if ! command -v python3 &>/dev/null; then
    die "Python 3 is not installed."
fi
note "$(python3 -V)"
if [[ $(python3 -c 'import sys; print(sys.version_info >= (3, 9, 2))') != True ]]; then
    die "Python version 3.9.2 or newer is required."
fi

#get the curret debian version info
piversion="${VERSION_ID:-0}"
if [[ "$piversion" -lt 11 ]]; then
    die "PiOS 11 (Bullseye) or newer is required for this script to work."
fi

if [ -d "$dir" ]; then
    note "Existing install found in $dir: this will be an update"
    TOTAL=6
else
    note "No existing install: this will be a fresh install"
    TOTAL=8
fi

step "Checking administrator access"
ensure_sudo

step "Updating the list of available software"
note "${DIM}Slow on a first run, or while the Pi is still doing its own updates.${RESET}"
run_spin "apt update" sudo apt-get update -qq -o DPkg::Lock::Timeout=300 || warn "apt update failed, carrying on with what is already known"

# Install python packages listed in requirements.txt into the active venv.
# $1: "" for a fresh install, "--upgrade" when updating
install_requirements()
{
    local upgrade="$1"
    local line n=0 total
    local -a args
    total=$(grep -c -v -E '^[[:space:]]*(#|$)' "$dir/requirements.txt")
    while IFS= read -r line || [ -n "$line" ]; do
        # skip blank lines and comments
        [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
        read -r -a args <<< "$line"
        n=$((n + 1))
        # vpype pulls in numpy, scipy and others, which can take many minutes on a Pi Zero
        if ! run_spin "Python package $n/$total: ${args[*]}" python3 -m pip install --prefer-binary $upgrade "${args[@]}"; then
            failed_packages=1
        fi
    done < "$dir/requirements.txt"
}

# Point the service file at the current user and install + start it
setup_service()
{
    current_user=$(whoami)
    if [ "$current_user" != "pi" ]; then
        note "Setting the service up for user '$current_user' instead of 'pi'"
        sed -i -e "s#^User=pi\$#User=$current_user#" -e "s#/home/pi/#$HOME/#g" "$dir/webplotter.service"
    fi

    sudo cp "$dir/webplotter.service" /etc/systemd/system/
    sudo systemctl daemon-reload
    if sudo systemctl enable webplotter --quiet; then
        ok "Web Plotter will start on boot"
    else
        echo "  ${RED}[fail]${RESET} Failed to enable the WebPlotter service!" >&2
    fi

    # systemd is not running inside containers / chroots (e.g. the CI image)
    if [ -d /run/systemd/system ]; then
        sudo systemctl restart webplotter
        sleep 2
        if sudo systemctl is-active --quiet webplotter; then
            IP_ADDRESS=$(hostname -I | awk '{print $1}')
            ok "Web Plotter is running"
            echo ""
            echo "${BOLD}${GREEN} All done!${RESET} Web plotter can be found at ${BOLD}http://$IP_ADDRESS:5000${RESET}"
        else
            echo "${BOLD}${RED} Something has gone wrong..... The last log lines:${RESET}"
            sudo journalctl -u webplotter -n 30 --no-pager
            exit 1
        fi
    else
        warn "systemd is not running, skipping service start."
    fi
}

reboot_pi()
{
    [ "$ISTTY" -eq 1 ] && printf '\033[?25h'
    if [ -n "$WEBPLOTTER_NO_REBOOT" ]; then
        echo " WEBPLOTTER_NO_REBOOT is set, not rebooting."
        return
    fi
    echo ""
    printf " Rebooting in 5 sec "

    (for i in $(seq 4 -1 1); do
        sleep 1;
        printf ".";
    done;)
    echo ""
    echo ""
    echo " Rebooting"
    sleep 1
    sudo reboot
}

failed_packages=0

## Check for dir, if not found do a fresh install ##
if [ ! -d "$dir" ] ; then

    step "Installing system packages"
    note "${DIM}git, python venv, libgeos, hp2xx and friends${RESET}"
    # libgeos-dev pulls in the matching libgeos runtime library for each release
    run_spin "apt install" env DEBIAN_FRONTEND=noninteractive LC_ALL=C LANG=C sudo apt-get install -qq -y \
            -o DPkg::Lock::Timeout=300 \
            git \
            python3-pip \
            libopenblas-dev \
            libgeos-dev \
            python3-venv \
            libssl-dev \
            poppler-utils \
            hp2xx || die "Failed to install system packages. Exiting"

    # Only the timelapse needs it, and it is large: a failure is not fatal
    run_spin "Installing ffmpeg (timelapse videos)" env DEBIAN_FRONTEND=noninteractive LC_ALL=C LANG=C sudo apt-get install -qq -y -o DPkg::Lock::Timeout=300 ffmpeg || warn "Could not install ffmpeg, so a timelapse keeps its pictures but no video is made"

    step "Downloading Web Plotter ($BRANCH branch) from GitHub"
    if run_spin "Checking the $BRANCH branch exists" env GIT_TERMINAL_PROMPT=0 git ls-remote --exit-code --heads "$git" "$BRANCH"; then
        run_spin "Downloading to $dir" env GIT_TERMINAL_PROMPT=0 git clone -q -b "$BRANCH" "$git" "$dir" || die "Download failed"
    else
        die "Branch $BRANCH does not exist, or GitHub could not be reached"
    fi

    step "Creating the Python virtual environment"
    run_spin "python3 -m venv" python3 -m venv "$venv" || die "Could not create the virtual environment."
    source "$venv/bin/activate"
    if [ -n "$VIRTUAL_ENV" ]; then
        note "Active: $VIRTUAL_ENV"
    else
        die "Virtual environment could not be activated."
    fi

    step "Installing Python packages"
    note "${DIM}The slowest step, especially vpype. A Pi Zero may need 10+ minutes here.${RESET}"
    run_spin "Fixing pip certificates" python3 -m pip install pip_system_certs -q
    install_requirements ""
    [ "$failed_packages" -eq 0 ] || die "Some python packages failed to install. Exiting"

    step "Preparing the web plotter config"
    cp "$dir/config.ini.sample" "$dir/config.ini"
    ok "config.ini created"

    step "Setting up the service"
    setup_service

    reboot_pi


##########################################
###      UPDATE EXISTING INSTALL      ####
##########################################


else
    step "Downloading the latest version ($BRANCH branch)"
    if [ ! -d "$venv/" ]; then
        warn "No penplotter_venv virtual environment found, creating one."
        run_spin "python3 -m venv" python3 -m venv "$venv" || die "Could not create the virtual environment."
    fi

    run_spin "Checking the $BRANCH branch exists" env GIT_TERMINAL_PROMPT=0 git ls-remote --exit-code --heads "$git" "$BRANCH" || die "Branch $BRANCH does not exist, or GitHub could not be reached"

    # Download into a new directory first so a failed download can't destroy the working install
    new="$dir.new"
    old="$dir.old"
    rm -rf "$new" "$old"
    run_spin "Downloading to $new" env GIT_TERMINAL_PROMPT=0 git clone -q -b "$BRANCH" "$git" "$new" || { rm -rf "$new"; die "Download failed, nothing was changed."; }

    step "Keeping your uploads, settings and plot history"
    # add user files back
    if [ -d "$dir/uploads" ]; then
        rm -rf "$new/uploads"
        cp -a "$dir/uploads" "$new/uploads" || { rm -rf "$new"; die "Could not copy your uploads, nothing was changed."; }
        ok "uploads kept"
    else
        warn "$dir/uploads/ does not exist. No files moved."
    fi
    if [ -e "$dir/config.ini" ]; then
        cp -a "$dir/config.ini" "$new/config.ini" || { rm -rf "$new"; die "Could not copy your config.ini, nothing was changed."; }
        ok "config.ini kept"
    else
        cp "$new/config.ini.sample" "$new/config.ini"
        ok "config.ini created"
    fi

    # Plotters added by the user (userdata/plotters.json)
    if [ -d "$dir/userdata" ]; then
        rm -rf "$new/userdata"
        cp -a "$dir/userdata" "$new/userdata" || { rm -rf "$new"; die "Could not copy your plotters, nothing was changed."; }
        ok "your plotters kept"
    fi

    # Recorded timelapses
    if [ -d "$dir/timelapse" ]; then
        rm -rf "$new/timelapse"
        cp -a "$dir/timelapse" "$new/timelapse" || { rm -rf "$new"; die "Could not copy your timelapses, nothing was changed."; }
        ok "timelapses kept"
    fi

    if [ -e "$dir/history.db" ]; then
        cp -a "$dir/history.db" "$new/history.db" || { rm -rf "$new"; die "Could not copy your plot history, nothing was changed."; }
        ok "plot history kept"
    fi

    sudo systemctl stop webplotter 2>/dev/null
    mv "$dir" "$old" && mv "$new" "$dir" || die "Could not replace $dir. Your old install is in $old"
    ok "New version in place"

    # Added after the first release: PDF import needs it, nothing else does
    if ! command -v pdftocairo >/dev/null 2>&1; then
        run_spin "Installing poppler-utils (PDF import)" env DEBIAN_FRONTEND=noninteractive LC_ALL=C LANG=C sudo apt-get install -qq -y -o DPkg::Lock::Timeout=300 poppler-utils || warn "Could not install poppler-utils, so PDF files cannot be imported"
    fi

    # Added after the first release: timelapse videos need it, nothing else does
    if ! command -v ffmpeg >/dev/null 2>&1; then
        run_spin "Installing ffmpeg (timelapse videos)" env DEBIAN_FRONTEND=noninteractive LC_ALL=C LANG=C sudo apt-get install -qq -y -o DPkg::Lock::Timeout=300 ffmpeg || warn "Could not install ffmpeg, so a timelapse keeps its pictures but no video is made"
    fi

    step "Updating Python packages"
    source "$venv/bin/activate"
    if [ -n "$VIRTUAL_ENV" ]; then
        note "Active: $VIRTUAL_ENV"
    else
        die "Virtual environment could not be activated. Your old install is in $old"
    fi
    run_spin "Fixing pip certificates" python3 -m pip install --upgrade pip_system_certs
    install_requirements "--upgrade"
    if [ "$failed_packages" -ne 0 ]; then
        warn "Some packages failed to update. Your old install is kept in $old"
    else
        rm -rf "$old"
    fi

    step "Restarting the service"
    sudo rm -f /etc/systemd/system/webplotter.service
    setup_service

    reboot_pi
fi
