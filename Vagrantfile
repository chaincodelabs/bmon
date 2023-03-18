Vagrant.configure("2") do |config|

  ssh_pub_key = File.readlines("#{Dir.home}/.ssh/id_rsa_yubikey.pub").first.strip

  config.vm.provision "shell" do |s|
    s.inline = <<-SHELL
        echo #{ssh_pub_key} >> /home/vagrant/.ssh/authorized_keys
        mkdir -p /root/.ssh
        echo #{ssh_pub_key} >> /root/.ssh/authorized_keys
        apt-get update && apt-get install --yes curl sudo
    SHELL
  end

  config.vm.provider "virtualbox" do |vb|
    vb.memory = "1024"
  end

  config.vm.define "bmon-server" do |box|
    box.vm.network "private_network", ip: "192.168.56.2"
    box.vm.hostname = "bmon-server"
    box.vm.box = "debian/testing64"
  end

  config.vm.define "bmon-b1" do |box|
    box.vm.network "private_network", ip: "192.168.56.3"
    box.vm.hostname = "bmon-b1"
    box.vm.box = "debian/testing64"
  end

  config.vm.define "bmon-b2" do |box|
    box.vm.network "private_network", ip: "192.168.56.4"
    box.vm.hostname = "bmon-b2"
    box.vm.box = "debian/testing64"
  end

end
