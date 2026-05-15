#!/usr/bin/env bash
set -Eeuo pipefail

# Installs Docker Engine from Docker's Ubuntu apt repository, then installs and
# configures the NVIDIA Container Toolkit for Docker.
#
# Sources:
# - https://docs.docker.com/engine/install/ubuntu/#install-using-the-repository
# - https://docs.docker.com/engine/install/linux-postinstall/
# - https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html
#
# Usage:
#   bash install-docker-nvidia-toolkit.sh
#
# Optional:
#   NVIDIA_CONTAINER_TOOLKIT_VERSION=1.19.0-1 bash install-docker-nvidia-toolkit.sh
#   RUN_DOCKER_HELLO_WORLD=0 RUN_GPU_TEST=0 bash install-docker-nvidia-toolkit.sh

DOCKER_GROUP_WRAPPER=0

log() {
  printf '\n==> %s\n' "$*"
}

warn() {
  printf '\nWARNING: %s\n' "$*" >&2
}

die() {
  printf '\nERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

run_docker() {
  local cmd

  if [[ "$DOCKER_GROUP_WRAPPER" == "1" ]]; then
    if command -v newgrp >/dev/null 2>&1; then
      printf -v cmd '%q ' docker "$@"
      newgrp docker <<EOF
$cmd
EOF
      return $?
    fi

    warn "Could not activate the docker group in this shell because 'newgrp' is missing."
    warn "Falling back to sudo for this Docker command. Log out and back in after the script."
    sudo docker "$@"
    return $?
  fi

  if docker "$@"; then
    return 0
  fi

  if command -v newgrp >/dev/null 2>&1; then
    printf -v cmd '%q ' docker "$@"
    log "Retrying Docker command inside the docker group with newgrp."
    newgrp docker <<EOF
$cmd
EOF
    return $?
  fi

  warn "Could not activate the docker group in this shell because 'newgrp' is missing."
  warn "Falling back to sudo for this Docker command. Log out and back in after the script."
  sudo docker "$@"
}

restart_docker() {
  if [[ "$(ps -p 1 -o comm= 2>/dev/null || true)" == "systemd" ]] && command -v systemctl >/dev/null 2>&1; then
    sudo systemctl enable --now docker.service
    sudo systemctl enable containerd.service
    sudo systemctl restart docker
    return
  fi

  if command -v service >/dev/null 2>&1; then
    sudo service docker restart || sudo service docker start
    return
  fi

  die "Could not restart Docker. This script expected systemd or the service command."
}

install_docker_engine() {
  local arch
  local codename
  local -a conflicting_packages

  log "Removing conflicting distro Docker packages, if any."
  mapfile -t conflicting_packages < <(
    dpkg --get-selections \
      docker.io \
      docker-compose \
      docker-compose-v2 \
      docker-doc \
      podman-docker \
      containerd \
      runc 2>/dev/null | awk '{print $1}'
  )

  if ((${#conflicting_packages[@]})); then
    sudo apt-get remove -y "${conflicting_packages[@]}"
  else
    log "No conflicting Docker packages found."
  fi

  log "Installing Docker apt prerequisites."
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl gnupg

  log "Adding Docker's official apt repository."
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc

  arch="$(dpkg --print-architecture)"
  # shellcheck disable=SC1091
  codename="$(. /etc/os-release && printf '%s' "${UBUNTU_CODENAME:-$VERSION_CODENAME}")"

  sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${codename}
Components: stable
Architectures: ${arch}
Signed-By: /etc/apt/keyrings/docker.asc
EOF

  log "Installing Docker Engine, CLI, containerd, Buildx, and Compose plugin."
  sudo apt-get update
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

  log "Starting Docker."
  restart_docker
}

configure_docker_group() {
  log "Adding ${USER} to the docker group."
  sudo groupadd -f docker
  sudo usermod -aG docker "$USER"

  if id -nG | tr ' ' '\n' | grep -qx docker; then
    log "This shell already has docker group access."
  else
    log "The docker group was updated. This script will use 'newgrp docker' when it needs fresh group access."
    DOCKER_GROUP_WRAPPER=1
    warn "Open a new terminal after this script so normal Docker commands work without sudo."
  fi
}

install_nvidia_container_toolkit() {
  local version="${NVIDIA_CONTAINER_TOOLKIT_VERSION:-}"

  log "Adding NVIDIA Container Toolkit apt repository."
  sudo apt-get update
  sudo apt-get install -y --no-install-recommends ca-certificates curl gnupg

  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | sudo gpg --batch --yes --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

  curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null

  log "Installing NVIDIA Container Toolkit packages."
  sudo apt-get update

  if [[ -n "$version" ]]; then
    sudo apt-get install -y \
      "nvidia-container-toolkit=${version}" \
      "nvidia-container-toolkit-base=${version}" \
      "libnvidia-container-tools=${version}" \
      "libnvidia-container1=${version}"
  else
    sudo apt-get install -y \
      nvidia-container-toolkit \
      nvidia-container-toolkit-base \
      libnvidia-container-tools \
      libnvidia-container1
  fi

  log "Configuring NVIDIA Container Toolkit for Docker."
  sudo nvidia-ctk runtime configure --runtime=docker
  restart_docker
}

verify_installation() {
  if [[ "${RUN_DOCKER_HELLO_WORLD:-1}" == "1" ]]; then
    log "Verifying Docker with hello-world."
    run_docker run --rm hello-world
  fi

  if [[ "${RUN_GPU_TEST:-1}" != "1" ]]; then
    log "Skipping GPU container test because RUN_GPU_TEST is not 1."
    return
  fi

  if ! command -v nvidia-smi >/dev/null 2>&1; then
    warn "nvidia-smi was not found on the host. Install the NVIDIA driver, then run:"
    warn "docker run --rm --gpus all ubuntu nvidia-smi"
    return
  fi

  log "Verifying GPU access from Docker."
  run_docker run --rm --gpus all ubuntu nvidia-smi
}

main() {
  [[ "${EUID}" -ne 0 ]] || die "Run this as your normal user, not with sudo, so USER names the account to add to the docker group."
  [[ -n "${USER:-}" ]] || die "USER is not set."

  require_command apt-get
  require_command dpkg
  require_command awk
  require_command grep
  require_command sed
  require_command sudo
  require_command getconf

  if [[ ! -r /etc/os-release ]]; then
    die "/etc/os-release is missing; this script expects Ubuntu."
  fi

  # shellcheck disable=SC1091
  . /etc/os-release
  [[ "${ID:-}" == "ubuntu" ]] || die "This script expects Ubuntu. Detected ID='${ID:-unknown}'."
  [[ "$(getconf LONG_BIT)" == "64" ]] || die "Docker Engine for Ubuntu requires a 64-bit OS."

  log "Installing for user: ${USER}"
  log "Requesting sudo once up front. You may be prompted for your password."
  sudo -v

  install_docker_engine
  configure_docker_group
  install_nvidia_container_toolkit
  verify_installation

  log "Done. Docker and NVIDIA Container Toolkit are installed and Docker is configured for GPU containers."
  log "Open a new terminal before relying on docker group membership outside this script."
}

main "$@"
