# @summary Prometheus monitoring of smokescreen servers
#
class zulip_ops::prometheus::smokescreen {
  $version = $zulip::common::versions['statsd_exporter']['version']
  $dir = "/srv/zulip-statsd_exporter-${version}"
  $bin = "${dir}/statsd_exporter"
  $conf = '/etc/zulip/statsd_exporter.yaml'

  zulip::external_dep { 'statsd_exporter':
    version        => $version,
    url            => "https://github.com/prometheus/statsd_exporter/releases/download/v${version}/statsd_exporter-v${version}.linux-${zulip::common::goarch}.tar.gz",
    tarball_prefix => "statsd_exporter-v${version}.linux-${zulip::common::goarch}",
  }

  zulip_ops::firewall_allow { 'statsd_exporter': port => '9102' }
  file { $conf:
    ensure  => file,
    require => User[zulip],
    owner   => 'zulip',
    group   => 'zulip',
    mode    => '0644',
    source  => 'puppet:///modules/zulip_ops/statsd_exporter.yaml',
  }
  file { "${zulip::common::supervisor_conf_dir}/prometheus_statsd_exporter.conf":
    ensure  => file,
    require => [
      User[zulip],
      Package[supervisor],
      Zulip::External_Dep['statsd_exporter'],
    ],
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    content => template('zulip_ops/supervisor/conf.d/prometheus_statsd_exporter.conf.template.erb'),
    notify  => Service[supervisor],
  }
}
