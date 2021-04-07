# Testing the installer

Zulip's install process is tested as part of [its continuous
integrations suite][CI], but that only tests the most common
configurations; when making changes to more complicated [installation
options][installer-docs], Zulip provides tooling to repeatedly test
the installation process in a clean environment each time.

[CI]: https://github.com/zulip/zulip/actions/workflows/production-suite.yml?query=branch%3Amaster
[installer-docs]: ../production/install.md

## Configuring

Using the test installer framework requires a Linux operating system;
it will not work on WSL, for instance.  It requires at least 3G of
RAM, in order to accommodate the VMs and the steps which build the
release assets.

To begin, install the LXC toolchain:
```
sudo apt-get install lxc lxc-utils
```

All LXC commands (and hence many parts of the test installer) will
need to be run as root.

## Running a test install

The `test-install` tooling takes a distribution release name
(e.g. "focal" or "bionic") and any of the normal options you want to pass down
into the installer.

For example, to test an install onto Ubuntu 20.04 "Focal", we might
call:
```
sudo ./tools/test-install/install \
  focal \
  --hostname=zulip.example.net \
  --email=username@example.net
```

The first time you run this command for a given distribution, it will
build a "base" image for that to use on subsequent times; this will
take a while.

## See running containers after installation

Regardless of if the install succeeds or fails, it will stay running
so you can inspect it. You can see all of the containers which are
running, and their randomly-generated names, by running:
```
sudo lxc-ls -f
```

## Connect to a running container

After using `lxc-ls` to list containers, you can choose one of them
and connect to its terminal:
```
sudo lxc-attach --clear-env -n zulip-install-focal-PUvff
```

## Stopping and destroying containers

To destroy all containers (but leave the base containers, which speed
up the initial install):
```
sudo ./tools/test-install/destroy-all -f
```

To destroy just one container:
```
sudo lxc-destroy -f -n zulip-install-focal-PUvff
```

