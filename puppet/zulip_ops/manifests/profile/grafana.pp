# @summary Observability using Grafana
#
class zulip_ops::profile::grafana {
  include zulip_ops::profile::base
  include zulip::supervisor

  $version = '7.1.0'
  zulip::sha256_tarball_to { 'grafana':
    url     => "https://dl.grafana.com/oss/release/grafana-${version}.linux-amd64.tar.gz",
    sha256  => '4b6d6ce3670b281919dac8da4bf6d644bc8403ceae215e4fd10db0f2d1e5718e',
    install => {
      "grafana-${version}/" => "/opt/grafana-${version}/",
    },
  }
  file { '/opt/grafana':
    ensure  => 'link',
    target  => "/opt/grafana-${version}/",
    require => Zulip::Sha256_tarball_to['grafana'],
  }

  group { 'grafana':
    ensure => present,
    gid    => '1070',
  }
  user { 'grafana':
    ensure     => present,
    uid        => '1070',
    gid        => '1070',
    shell      => '/bin/bash',
    home       => '/opt/grafana',
    managehome => false,
  }
  file { '/var/lib/grafana':
    ensure  => directory,
    owner   => 'grafana',
    group   => 'grafana',
    require => [ User[grafana], Group[grafana] ],
  }
  file { '/var/log/grafana':
    ensure => directory,
    owner  => 'grafana',
    group  => 'grafana',
  }

  file { '/etc/supervisor/conf.d/grafana.conf':
    ensure  => file,
    require => [
      Package[supervisor],
      File['/opt/grafana'],
      File['/var/lib/grafana'],
      File['/var/log/grafana'],
    ],
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    source  => 'puppet:///modules/zulip_ops/supervisor/conf.d/grafana.conf',
    notify  => Service[supervisor],
  }

  file { '/etc/grafana':
    ensure => directory,
    owner  => 'root',
    group  => 'root',
    mode   => '0644',
  }
  file { '/etc/grafana/grafana.ini':
    ensure => file,
    owner  => 'root',
    group  => 'root',
    mode   => '0644',
    source => 'puppet:///modules/zulip_ops/grafana/grafana.ini',
    notify => Service[supervisor],
  }
}
