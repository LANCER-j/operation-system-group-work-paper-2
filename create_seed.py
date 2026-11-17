import sys, pathlib, subprocess, hashlib, io, json
ROOT=pathlib.Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'tools'/'python'))
import pycdlib
v=ROOT/'vm'
name='ubuntu-18.04-minimal-cloudimg-amd64.img'
line=next(x for x in (v/'SHA256SUMS').read_text().splitlines() if x.split()[-1].lstrip('*')==name)
expected=line.split()[0]
actual=hashlib.sha256((v/'ubuntu18-base.qcow2').read_bytes()).hexdigest()
assert expected==actual, (expected,actual)
(v/'verified-image.json').write_text(json.dumps({'upstream_name':name,'sha256':actual,'source':'https://cloud-images.ubuntu.com/minimal/releases/bionic/release-20230502/','verified':True},indent=2))
key=v/'guest_ed25519'
if not key.exists():
 subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-C','paper02-local-guest','-f',str(key)],check=True)
pub=key.with_suffix('.pub').read_text().strip()
user='''#cloud-config
hostname: paper02-build
manage_etc_hosts: true
apt:
  preserve_sources_list: true
users:
  - name: researcher
    groups: [adm, sudo]
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    lock_passwd: true
    ssh_authorized_keys:
      - KEY
ssh_pwauth: false
disable_root: true
write_files:
  - path: /etc/apt/sources.list
    permissions: '0644'
    content: |
      deb http://repo.huaweicloud.com/ubuntu bionic main universe restricted multiverse
      deb http://repo.huaweicloud.com/ubuntu bionic-updates main universe restricted multiverse
      deb http://repo.huaweicloud.com/ubuntu bionic-security main universe restricted multiverse
  - path: /root/paper02-setup.sh
    permissions: '0700'
    content: |
      #!/bin/bash
      set -eux
      export DEBIAN_FRONTEND=noninteractive
      apt-get update
      apt-get install -y build-essential libncurses5-dev bc bison flex libssl-dev libelf-dev python3 sysbench mysql-server-5.7 git initramfs-tools curl
      systemctl stop mysql
      systemctl disable mysql
      touch /root/paper02-setup-complete
runcmd:
  - [bash, -c, '/root/paper02-setup.sh > /var/log/paper02-setup.log 2>&1']
'''.replace('KEY',pub)
meta='instance-id: paper02-build-20261011\nlocal-hostname: paper02-build\n'
iso=pycdlib.PyCdlib();iso.new(interchange_level=3,joliet=3,rock_ridge='1.09',vol_ident='cidata')
for name,data in [('user-data',user),('meta-data',meta)]:
 raw=data.encode();iso.add_fp(io.BytesIO(raw),len(raw),iso_path='/'+name.upper().replace('-','_')+';1',rr_name=name,joliet_path='/'+name)
iso.write(str(v/'seed.iso'));iso.close()
print('Ubuntu SHA256 verified; SSH-only cloud-init ISO created.')
