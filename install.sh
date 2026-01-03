#!/bin/bash

# This script automates the installation and setup of the FermController Flask application
# on a Raspberry Pi, including systemd service and Chromium kiosk mode.

# --- Configuration ---
# This script must be run with sudo, which sets the SUDO_USER variable.
# We use this to determine the original user's home directory.
if [ -z "$SUDO_USER" ]; then
    echo -e "\e[31m[ERROR]\e[0m This script must be run with 'sudo ./install.sh', not via 'sudo su' or a direct root login."
    exit 1
fi
ORIGINAL_USER="$SUDO_USER"
ORIGINAL_USER_HOME=$(eval echo "~$ORIGINAL_USER")
ORIGINAL_USER_GROUP=$(id -gn "$ORIGINAL_USER")
APP_DIR="$ORIGINAL_USER_HOME/fermcontroller"
SERVICE_FILE="/etc/systemd/system/fermcontroller.service"
KIOSK_SCRIPT="$ORIGINAL_USER_HOME/launch_kiosk.sh"
AUTOSTART_DESKTOP_FILE="$ORIGINAL_USER_HOME/.config/autostart/fermcontroller-kiosk.desktop"

# --- Functions ---
log_info() {
    echo -e "\e[32m[INFO]\e[0m $1"
}

log_warn() {
    echo -e "\e[33m[WARN]\e[0m $1"
}

log_error() {
    echo -e "\e[31m[ERROR]\e[0m $1"
    exit 1
}

confirm() {
    read -r -p "$1 [y/N] " response
    case "$response" in
        [yY][eE][sS]|[yY])
            true
            ;;
        *)
            false
            ;;
    esac
}

# --- Main Script ---
log_info "Starting FermController installation script..."

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    log_error "This script must be run with root privileges. Please run: sudo ./install.sh"
fi

# Confirm with user
if ! confirm "This script will install dependencies, copy files, and configure system services. Continue?"; then
    log_info "Installation cancelled by user."
    exit 0
fi

# 1. System Updates
log_info "Updating system packages..."
apt update || log_error "Failed to update apt."
apt upgrade -y || log_error "Failed to upgrade apt packages."

# 2. Install Dependencies
log_info "Installing required system packages (python3-pip, python3-venv, chromium-browser)..."
apt install -y python3-pip python3-venv chromium-browser || log_error "Failed to install system dependencies."

# Install libcap2-bin for setcap, which allows running on port 80 without root
log_info "Installing libcap2-bin for secure port access..."
apt install -y libcap2-bin || log_error "Failed to install libcap2-bin."

# 3. Copy Application Files
log_info "Copying FermController application to $APP_DIR..."
if [ -d "$APP_DIR" ]; then
    log_warn "$APP_DIR already exists. Removing old directory..."
    rm -rf "$APP_DIR" || log_error "Failed to remove old application directory."
fi
# Assuming the script is run from the fermcontroller project root
cp -r "$(pwd)" "$APP_DIR" || log_error "Failed to copy application files."
chown -R "$ORIGINAL_USER":"$ORIGINAL_USER_GROUP" "$APP_DIR" || log_error "Failed to set ownership for application directory."

# 4. Setup Python Virtual Environment and Install Python Dependencies
log_info "Setting up Python virtual environment and installing Python dependencies..."
cd "$APP_DIR" || log_error "Failed to change to application directory."
sudo -u "$ORIGINAL_USER" python3 -m venv venv || log_error "Failed to create virtual environment."
PYTHON_EXEC="$APP_DIR/venv/bin/python3"
PIP_EXEC="$APP_DIR/venv/bin/pip"
sudo -u "$ORIGINAL_USER" "$PIP_EXEC" install Flask RPi.GPIO w1thermsensor || log_error "Failed to install Python dependencies."

# Grant the python executable capability to bind to privileged ports (<1024)
log_info "Granting permission to bind to port 80 without running as root..."
REAL_PYTHON_EXEC=$(readlink -f "$PYTHON_EXEC")
log_info "Setting capabilities on real Python executable: $REAL_PYTHON_EXEC"
setcap 'cap_net_bind_service=+ep' "$REAL_PYTHON_EXEC" || log_error "Failed to set capabilities on Python executable. Target: $REAL_PYTHON_EXEC"

# 5. Add user to required groups for hardware access
log_info "Adding user '$ORIGINAL_USER' to 'gpio' and 'dialout' groups for hardware access..."
usermod -a -G gpio,dialout "$ORIGINAL_USER" || log_warn "Failed to add user to gpio/dialout groups. This might be okay."

# 6. Create Systemd Service for Flask App
log_info "Creating systemd service for FermController..."
cat <<EOF > "$SERVICE_FILE"
[Unit]
Description=FermController Flask App
After=network.target
After=multi-user.target
After=graphical.target # Ensure graphical target is up for kiosk mode

[Service]
Type=simple
User=$ORIGINAL_USER
Group=$ORIGINAL_USER_GROUP
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python3 $APP_DIR/app.py --no-simulation
Restart=on-failure
RestartSec=5s
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
chmod 644 "$SERVICE_FILE" || log_error "Failed to set permissions for service file."
systemctl daemon-reload || log_error "Failed to reload systemd daemon."
systemctl enable fermcontroller.service || log_error "Failed to enable fermcontroller service."
systemctl start fermcontroller.service || log_error "Failed to start fermcontroller service."
log_info "FermController systemd service created and started."
log_info "You can check its status with: sudo systemctl status fermcontroller.service"
log_info "And view logs with: sudo journalctl -u fermcontroller.service -f"

# 7. Configure Chromium Kiosk Mode Autostart
log_info "Configuring Chromium for kiosk mode autostart..."

# Create launch_kiosk.sh
cat <<EOF > "$KIOSK_SCRIPT"
#!/bin/bash
# Wait for Flask app to start and network to be ready. Adjust sleep duration if needed.
sleep 15
# Launch Chromium in kiosk mode
/usr/bin/chromium-browser --disable-pinch --noerrdialogs --disable-infobars --kiosk --app=http://localhost # If you change WEB_PORT in app_config.py, update the URL here (e.g., http://localhost:5000)
EOF
chown "$ORIGINAL_USER":"$ORIGINAL_USER_GROUP" "$KIOSK_SCRIPT" || log_error "Failed to set ownership for kiosk script."
chmod +x "$KIOSK_SCRIPT" || log_error "Failed to make kiosk script executable."

# Create autostart .desktop file
mkdir -p "$ORIGINAL_USER_HOME/.config/autostart" || log_error "Failed to create autostart directory."
chown -R "$ORIGINAL_USER":"$ORIGINAL_USER_GROUP" "$ORIGINAL_USER_HOME/.config" || log_error "Failed to set ownership for .config directory."

cat <<EOF > "$AUTOSTART_DESKTOP_FILE"
[Desktop Entry]
Type=Application
Name=FermController Kiosk
Comment=Launch FermController in Kiosk Mode
Exec=$KIOSK_SCRIPT
Terminal=false
StartupNotify=false
EOF
chown "$ORIGINAL_USER":"$ORIGINAL_USER_GROUP" "$AUTOSTART_DESKTOP_FILE" || log_error "Failed to set ownership for autostart desktop file."

log_info "Chromium kiosk mode configured."

log_info "Installation complete!"
log_warn "Remember to enable the 1-Wire interface via 'sudo raspi-config' if you haven't already."
log_warn "A REBOOT IS REQUIRED for group permission changes to take effect."
log_info "Please reboot now: sudo reboot"
