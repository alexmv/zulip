# @summary Configures a node for monitoring with Prometheus
#
class zulip_ops::prometheus_node {
  include zulip::supervisor

  $version = '1.0.1'
  zulip::sha256_tarball_to { 'node_exporter':
    url     => "https://github.com/prometheus/node_exporter/releases/download/v${version}/node_exporter-${version}.linux-amd64.tar.gz",
    sha256  => '3369b76cd2b0ba678b6d618deab320e565c3d93ccb5c2a0d5db51a53857768ae',
    install => {
      "node_exporter-${version}.linux-amd64/node_exporter" => "/usr/local/bin/node_exporter-${version}",
    },
  }
  file { '/usr/local/bin/node_exporter':
    ensure => 'link',
    target => "/usr/local/bin/node_exporter-${version}",
  }

  group { 'prometheus':
    ensure => present,
    gid    => '1060',
  }
  user { 'prometheus':
    ensure     => present,
    uid        => '1060',
    gid        => '1060',
    shell      => '/bin/bash',
    home       => '/nonexistent',
    managehome => false,
  }
  file { '/etc/supervisor/conf.d/prometheus_node_exporter.conf':
    ensure  => file,
    require => [ User[prometheus], Package[supervisor] ],
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    source  => 'puppet:///modules/zulip_ops/supervisor/conf.d/prometheus_node_exporter.conf',
    notify  => Service[supervisor],
  }
}
