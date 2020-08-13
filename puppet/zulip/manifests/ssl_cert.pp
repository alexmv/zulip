class zulip::ssl_cert {
  case $::osfamily {
    'debian': {
      package { 'ssl-cert':
        ensure => 'installed',
      }

      exec { 'generate_snakeoil_cert':
        require => Package['ssl-cert'],
        creates => '/etc/ssl/certs/ssl-cert-snakeoil.pem',
        command => '/usr/sbin/make-ssl-cert generate-default-snakeoil',
      }
    }
    'redhat': {
      group { 'ssl-cert':
        ensure => present,
      }
      # allows ssl-cert group to read /etc/pki/tls/private
      file { '/etc/pki/tls/private':
        require => Group['ssl-cert'],
        ensure  => 'directory',
        mode    => '0640',
        owner   => 'root',
        group   => 'ssl-cert',
      }
    }
    default: {
      fail('osfamily not supported')
    }
  }
}
