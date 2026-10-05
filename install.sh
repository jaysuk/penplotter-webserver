#!/bin/bash

dir="$HOME/webplotter"
venv="$HOME/penplotter_venv"
git="${WEBPLOTTER_REPO:-https://github.com/ithinkido/penplotter-webserver.git}"

#######################################################
#######################################################

# Always give the cursor back, however the script ends
trap 'printf "\033[?25h"' EXIT
printf "\033[?25l"

die()
{
    echo -e "\e[1;31m $1\e[0m" >&2
    exit 1
}

# Show a spinner while a background process runs
spinner()
{
    local pid=$1
    local spinstr='|/-\'
    while kill -0 "$pid" 2>/dev/null; do
        local temp=${spinstr#?}
        printf " [%c]  " "$spinstr"
        local spinstr=$temp${spinstr%"$temp"}
        sleep 1
        printf "\b\b\b\b\b\b"
    done
    printf "    \b\b\b\b"
}

# Run a command quietly with a spinner. Returns the exit status of the command itself.
run_spin()
{
    "$@" > /dev/null 2>&1 &
    local pid=$!
    spinner $pid
    wait $pid
}

#System Info
. /etc/os-release 2>/dev/null
echo -e "\e[1m${PRETTY_NAME:-unknown OS}\e[0m"
echo -e "\e[1m$(getconf LONG_BIT)-bit OS\e[0m"

if ! command -v python3 &>/dev/null; then
    die "Python 3 is not installed."
fi
echo -e "\e[1m$(python3 -V)\e[0m"
if [[ $(python3 -c 'import sys; print(sys.version_info >= (3, 9, 2))') != True ]]; then
    die "Python version 3.9.2 or newer is required."
fi

#get the curret debian version info
piversion="${VERSION_ID:-0}"
if [[ "$piversion" -lt 11 ]]; then
    die "PiOS 11 (Bullseye) or newer is required for this script to work."
fi

# lsb_release is not installed on every image, so use os-release
codename="${VERSION_CODENAME:-$(lsb_release -cs 2>/dev/null)}"
[ -n "$codename" ] || die "Could not determine the OS release name."
BRANCH="${WEBPLOTTER_BRANCH:-${codename}_$(getconf LONG_BIT)}"

echo ""
echo "Updating apt. This will take a while..."
run_spin sudo apt-get update -qq
echo -e "\e[32m Done.\e[0m"

# Install python packages listed in requirements.txt into the active venv.
# $1: "" for a fresh install, "--upgrade" when updating
install_requirements()
{
    local upgrade="$1"
    local line
    local -a args
    while IFS= read -r line || [ -n "$line" ]; do
        # skip blank lines and comments
        [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
        read -r -a args <<< "$line"
        echo "Installing ${args[*]}"
        if run_spin python3 -m pip install --prefer-binary $upgrade "${args[@]}"; then
            echo -e "\e[32m ${args[*]} was installed successfully.\e[0m"
        else
            echo -e "\e[31m Failed to install ${args[*]}.\e[0m"
            failed_packages=1
        fi
        echo ""
    done < "$dir/requirements.txt"
}

# Point the service file at the current user and install + start it
setup_service()
{
    current_user=$(whoami)
    if [ "$current_user" != "pi" ]; then
        echo -e "\e[33m Fix user define\e[0m"
        echo "Setup user '$current_user' in webplotter.service"
        sed -i -e "s#^User=pi\$#User=$current_user#" -e "s#/home/pi/#$HOME/#g" "$dir/webplotter.service"
        echo ""
    fi

    echo "Setup auto start for Web Plotter on boot"
    sudo cp "$dir/webplotter.service" /etc/systemd/system/
    sudo systemctl daemon-reload
    if sudo systemctl enable webplotter --quiet; then
        echo "WebPlotter startup service enabled."
    else
        echo "Error: Failed to enable WebPlotter service!" >&2
    fi

    # systemd is not running inside containers / chroots (e.g. the CI image)
    if [ -d /run/systemd/system ]; then
        sudo systemctl restart webplotter
        sleep 2
        if sudo systemctl is-active --quiet webplotter; then
            IP_ADDRESS=$(hostname -I | awk '{print $1}')
            echo -e "\e[1m After reboot - Web plotter can be found at http://$IP_ADDRESS:5000\e[0m"
            echo ""
        else
            echo -e "\e[1;31m Something has gone wrong..... The last log lines:\e[0m"
            sudo journalctl -u webplotter -n 30 --no-pager
            exit 1
        fi
    else
        echo -e "\e[33m systemd is not running, skipping service start.\e[0m"
    fi
}

reboot_pi()
{
    printf "\033[?25h"
    printf "Rebooting in 5 sec "

    (for i in $(seq 4 -1 1); do
        sleep 1;
        printf ".";
    done;)
    echo ""
    echo ""
    echo "Rebooting"
    sleep 1
    sudo reboot
}

failed_packages=0

## Check for dir, if not found do a fresh install ##
if [ ! -d "$dir" ] ; then
    echo ""

    echo "Installing apt packages"

    # libgeos-dev pulls in the matching libgeos runtime library for each release
    if run_spin env LC_ALL=C LANG=C sudo apt-get install -qq -y \
            git \
            python3-pip \
            libopenblas-dev \
            libgeos-dev \
            python3-venv \
            libssl-dev \
            hp2xx; then
        echo -e "\e[32m Packages installed successfully.\e[0m"
    else
        die "Error: Failed to install packages. Exiting"
    fi
    echo ""

    echo "Downloading Web Plotter for $BRANCH from Github"
    if git ls-remote --exit-code --heads "$git" "$BRANCH" > /dev/null; then
        # DO NOT CHANGE without changing git actions
        git clone -q -b "$BRANCH" "$git" "$dir" || die "Download failed"
        echo -e "\e[32m Done.\e[0m"
    else
        die "Branch $BRANCH does not exist"
    fi
    echo ""

    echo "Creating python venv"
    python3 -m venv "$venv" || die "Could not create the virtual environment."
    source "$venv/bin/activate"
    if [ -n "$VIRTUAL_ENV" ]; then
        echo -e "\e[32m Pen plotter web server venv has been activated.\e[0m"
        echo " Path: $VIRTUAL_ENV"
    else
        die "Virtual environment could not be activated."
    fi
    echo ""

    echo "Fix pip certs"
    run_spin python3 -m pip install pip_system_certs -q
    echo -e "\e[32m Done.\e[0m"
    echo ""

    install_requirements ""
    [ "$failed_packages" -eq 0 ] || die "Some python packages failed to install. Exiting"

    echo "Preapre penplotter webserver config"
    cp "$dir/config.ini.sample" "$dir/config.ini"
    echo -e "\e[32m Done.\e[0m"
    echo ""

    setup_service

    reboot_pi


##########################################
###      UPDATE EXISTING INSTALL      ####
##########################################


else
    echo ""
    echo -e "\e[32m Directory $dir already exists\e[0m"
    echo ""
    if [ ! -d "$venv/" ]; then
        echo -e "\e[33m Looks like you do not have a penplotter_venv virtual environment.\e[0m"
        echo " Let's take care of that."
        python3 -m venv "$venv" || die "Could not create the virtual environment."
    fi

    echo "Updating pen plotter web server"
    git ls-remote --exit-code --heads "$git" "$BRANCH" > /dev/null || die "Branch $BRANCH does not exist"

    # Download into a new directory first so a failed download can't destroy the working install
    new="$dir.new"
    old="$dir.old"
    rm -rf "$new" "$old"
    # DO NOT CHANGE BRANCH NAME without changing git actions
    git clone -q -b "$BRANCH" "$git" "$new" || { rm -rf "$new"; die "Download failed, nothing was changed."; }

    # add user files back
    if [ -d "$dir/uploads" ]; then
        rm -rf "$new/uploads"
        cp -a "$dir/uploads" "$new/uploads" || { rm -rf "$new"; die "Could not copy your uploads, nothing was changed."; }
    else
        echo -e "\e[33m $dir/uploads/ does not exist. No files moved.\e[0m"
    fi
    if [ -e "$dir/config.ini" ]; then
        cp -a "$dir/config.ini" "$new/config.ini" || { rm -rf "$new"; die "Could not copy your config.ini, nothing was changed."; }
    else
        cp "$new/config.ini.sample" "$new/config.ini"
    fi

    sudo systemctl stop webplotter 2>/dev/null
    mv "$dir" "$old" && mv "$new" "$dir" || die "Could not replace $dir. Your old install is in $old"
    echo ""

    source "$venv/bin/activate"
    if [ -n "$VIRTUAL_ENV" ]; then
        echo -e "\e[32m Pen plotter web server venv has been activated.\e[0m"
        echo "Path: $VIRTUAL_ENV"
    else
        die "Virtual environment could not be activated. Your old install is in $old"
    fi
    echo ""

    run_spin python3 -m pip install --upgrade pip_system_certs
    install_requirements "--upgrade"
    if [ "$failed_packages" -ne 0 ]; then
        echo -e "\e[33m Some packages failed to update. Your old install is kept in $old\e[0m"
    else
        rm -rf "$old"
    fi

    sudo rm -f /etc/systemd/system/webplotter.service
    setup_service

    reboot_pi
fi
