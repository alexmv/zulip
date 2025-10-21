require "shellwords"

# Note that this is very slow (~350ms) and may get values which will
# rapidly go out of date, since settings are changed much more
# frequently than deploys -- in addition to potentially just not
# working if we're not on the application server.  We should generally
# avoid using this if at all possible.

Puppet::Functions.create_function(:get_django_setting_slow) do
  def get_django_setting_slow(name)
    if File.exist?("/etc/zulip/settings.py")
      output = `/home/zulip/deployments/current/scripts/get-django-setting #{name.shellescape} 2>&1`
      if $?.success?
        output.strip
      else
        nil
      end
    else
      nil
    end
  end
end
