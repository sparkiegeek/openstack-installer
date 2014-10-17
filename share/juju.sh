#
# juju.sh - Shell routines for Juju
#
# Copyright 2014 Canonical, Ltd.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

# Juju MAAS config
#
# configMaasEnvironment address credentials secret proxy
#
# See configureJuju
#
configMaasEnvironment()
{
	cat <<-EOF
default: maas

environments:
  maas:
    type: maas
    maas-server: 'http://$1/MAAS/'
    maas-oauth: '$2'
    admin-secret: $3
    default-series: trusty
    authorized-keys-path: ~/.ssh/id_rsa.pub
    apt-http-proxy: 'http://$1:8000/'
    lxc-clone: true
EOF
}

# Juju local config
#
# configLocalEnvironment
#
# See configureJuju
#
configLocalEnvironment()
{
	cat <<-EOF
default: local

environments:
  local:
    type: local
    container: kvm
    lxc-clone: true
    admin-secret: $1

  openstack:
    type: openstack
    use-floating-ip: true
    use-default-secgroup: true
    network: ubuntu-net
    auth-url: http://keystoneurl:5000/v2.0/
    tenant-name: ubuntu
    region: RegionOne
    auth-mode: userpass
    username: ubuntu
    password: $1
EOF
}

# Charm config
#
# configCharmOptions password
#
configCharmOptions()
{
	cat <<-EOF
keystone:
  admin-password: $1
  admin-user: 'admin'
juju-gui:
  password: $1
mysql:
  dataset-size: 512M
swift-proxy:
  zone-assignment: auto
  replicas: 3
  use-https: 'no'
swift-storage:
  zone: 1
  block-device: /etc/swift/storage.img|2G
quantum-gateway:
  instance-mtu: 1400
nova-cloud-controller:
  network-manager: Neutron
glance-simplestreams-sync:
  use_swift: False
EOF
}

# Configure Juju
#
# configureJuju type type-arguments
#
configureJuju()
{
	env_type=$1
	shift

	if [ ! -e "$INSTALL_HOME/.juju" ]; then
		mkdir -m 0700 "$INSTALL_HOME/.juju"
		chown "$INSTALL_USER:$INSTALL_USER" "$INSTALL_HOME/.juju"
	fi
	(umask 0077; $env_type $@ > "$INSTALL_HOME/.juju/environments.yaml")
	chown "$INSTALL_USER:$INSTALL_USER" \
	    "$INSTALL_HOME/.juju/environments.yaml"
}

# Bootstrap Juju inside container
#
# Creates a KVM, registers it in MAAS (as a virtual machine), then
# bootstraps Juju using normal MAAS provider process.
#
# jujuBootstrap uuid
#
# TODO break this function into smaller ones
jujuBootstrap()
{
	cluster_uuid=$1

        # ensure that maas can use virsh:
        usermod -a -G libvirtd maas
        service maas-clusterd restart

        virt-install --name juju-bootstrap --ram=2048 --vcpus=1 \
            --hvm --virt-type=kvm --pxe --boot network,hd \
            --os-variant=ubuntutrusty --graphics vnc --noautoconsole \
            --os-type=linux --accelerate \
            --disk=/var/lib/libvirt/images/juju-bootstrap.qcow2,bus=virtio,format=qcow2,cache=none,sparse=true,size=20 \
            --network=bridge=br0,model=virtio

        mac=$(virsh dumpxml juju-bootstrap |grep 'mac address' | cut -d\' -f2)

	# TODO dynamic architecture selection
	maas maas nodes new architecture=amd64/generic mac_addresses=$mac \
	    hostname=juju-bootstrap nodegroup=$cluster_uuid power_type=virsh \
            power_parameters_power_address=qemu:///system \
            power_parameters_power_id=juju-bootstrap

        system_id=$(nodeSystemId $mac)

	(cd "$INSTALL_HOME"; sudo -H -u "$INSTALL_USER" juju --show-log sync-tools)

        waitForNodeStatus $system_id 4
	(cd "$INSTALL_HOME"; sudo -H -u "$INSTALL_USER" juju bootstrap --upload-tools)&

        # wait for juju-bootstrap to be READY (6) in MAAS:
        waitForNodeStatus $system_id 6

}
