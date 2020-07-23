# @summary Gathers Prometheus statistics from all nodes.  Only one
# instance is necessary.
#
class zulip_ops::profile::prometheus_server {
  include zulip_ops::profile::base
  include zulip_ops::prometheus_node

  $version = '2.19.2'
  zulip::sha256_tarball_to { 'prometheus':
    url     => "https://github.com/prometheus/prometheus/releases/download/v${version}/prometheus-${version}.linux-amd64.tar.gz",
    sha256  => '68382959f73354b30479f9cc3e779cf80fd2e93010331652700dcc71f6b05586',
    install => {
      "prometheus-${version}.linux-amd64/" => "/opt/prometheus-${version}/",
    },
  }
  file { '/opt/prometheus':
    ensure  => 'link',
    target  => "/opt/prometheus-${version}/",
    require => Zulip::Sha256_tarball_to['prometheus'],
  }
  file { '/usr/local/bin/promtool':
    ensure  => 'link',
    target  => '/opt/prometheus/promtool',
    require => File['/opt/prometheus'],
  }

  file { '/var/lib/prometheus':
    ensure  => directory,
    owner   => 'prometheus',
    group   => 'prometheus',
    require => [ User[prometheus], Group[prometheus] ],
  }
  file { '/etc/supervisor/conf.d/prometheus.conf':
    ensure  => file,
    require => [
      Package[supervisor],
      File['/opt/prometheus'],
      File['/var/lib/prometheus'],
    ],
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    source  => 'puppet:///modules/zulip_ops/supervisor/conf.d/prometheus.conf',
    notify  => Service[supervisor],
  }

  file { '/etc/prometheus':
    ensure => directory,
    owner  => 'root',
    group  => 'root',
    mode   => '0644',
  }
  file { '/etc/prometheus/prometheus.yml':
    ensure => file,
    owner  => 'root',
    group  => 'root',
    mode   => '0644',
    source => 'puppet:///modules/zulip_ops/prometheus/prometheus.yml',
    notify => Service[supervisor],
  }
}
