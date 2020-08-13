class zulip::postgres_common {
  include zulip::common
  # Not 'require', not 'include', forcing ssl certs to have been configured first
  require zulip::ssl_cert
  case $::osfamily {
    'debian': {
      $postgresql = "postgresql-${zulip::base::postgres_version}"
      $postgres_packages = [
        # The database itself
        $postgresql,
        # tools for database monitoring; formerly ptop
        'pgtop',
        # our dictionary
        'hunspell-en-us',
        # Postgres Nagios check plugin
        'check-postgres',
        # Python modules used in our monitoring/worker threads
        'python3-dateutil', # TODO: use a virtualenv instead
      ]
    }
    'redhat': {
      $postgresql = "postgresql${zulip::base::postgres_version}"
      $postgres_packages = [
        $postgresql,
        "${postgresql}-server",
        "${postgresql}-devel",
        'pg_top',
        'hunspell-en-US',
        # exists on CentOS 6 and Fedora 29 but not CentOS 7
        # see https://pkgs.org/download/check_postgres
        # alternatively, download directly from:
        # https://bucardo.org/check_postgres/
        # 'check-postgres',  # TODO
      ]
      exec {'pip3_deps':
        command => 'python3 -m pip install python-dateutil',
      }
    }
    default: {
      fail('osfamily not supported')
    }
  }

  zulip::safepackage { $postgres_packages: ensure => 'installed' }

  if $::osfamily == 'debian' {
    # The logrotate file only created in debian-based systems
    exec { 'disable_logrotate':
      # lint:ignore:140chars
      command => '/usr/bin/dpkg-divert --rename --divert /etc/logrotate.d/postgresql-common.disabled --add /etc/logrotate.d/postgresql-common',
      # lint:endignore
      creates => '/etc/logrotate.d/postgresql-common.disabled',
    }
  }

  # Use arcane puppet virtual resources to add postgres user to zulip group
  @user { 'postgres':
    groups     => ['ssl-cert'],
    membership => minimum,
    require    => Package[$postgresql],
  }
  User <| title == postgres |> { groups +> 'zulip' }
}
