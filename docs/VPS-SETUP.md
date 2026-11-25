# Setting up a VPS

The end result is a web server on your VPS serving the store (over HTTPS when you have a domain name),
and a deploy user that DeltaPatcher reaches with an SSH key limited to SFTP. Each deploy sends only the
blobs the server does not have yet.

You need:
- a VPS with a public address and SSH (any Linux distribution; Windows Server works too, see the end)
- the OpenSSH client on your PC (built into Windows 10 and later, Linux and macOS)

A domain name is optional. With one pointing at the VPS, the web server gets an HTTPS certificate by
itself. Without one, players download from the server's IP address over plain http, for example
`http://203.0.113.10/`: see [No domain name](#no-domain-name). The examples say `game.example.com`;
without a domain name, put the IP address in its place.

The examples use Ubuntu or Debian and Caddy, which gets its HTTPS certificate by itself when it has a
domain name.

## Part 1: on the VPS

Log in as you normally do, then:

```
# a user that only deploys, and the folder the web server serves
sudo adduser --disabled-password --gecos "" deploy
sudo mkdir -p /var/www/game
sudo chown deploy:deploy /var/www/game

# where its key will go
sudo -u deploy mkdir -p /home/deploy/.ssh
sudo -u deploy touch /home/deploy/.ssh/authorized_keys
sudo chmod 700 /home/deploy/.ssh
sudo chmod 600 /home/deploy/.ssh/authorized_keys
```

The web server, with Caddy:

```
sudo apt install caddy
sudo nano /etc/caddy/Caddyfile
```

```
game.example.com {
    root * /var/www/game
    file_server
    @manifest path /manifest.json /manifest.previous.json
    header @manifest Cache-Control "no-cache"
    header /blobs/* Cache-Control "public, max-age=31536000, immutable"
    header /parts/* Cache-Control "public, max-age=31536000, immutable"
}
```

```
sudo systemctl reload caddy
sudo ufw allow 22,80,443/tcp      # if you use ufw
```

Write down the server's host key fingerprint. You will compare it in Part 3:

```
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

Prefer nginx? Use this server block (and certbot for HTTPS):

```
server {
    server_name game.example.com;
    root /var/www/game;
    autoindex off;
    location = /manifest.json { add_header Cache-Control "no-cache"; }
    location /blobs/ { add_header Cache-Control "public, max-age=31536000, immutable"; }
    location /parts/ { add_header Cache-Control "public, max-age=31536000, immutable"; }
    location /sigs/ { add_header Cache-Control "public, max-age=31536000, immutable"; }
    location ~ /\. { deny all; }
}
```

Apache (shared hosting included)? Nothing to write: every publish puts a `.htaccess` in the store and
deploys send it along. It turns listings off, gives blobs a type, sets the cache headers and refuses
everything that is not part of the release. The host must allow `.htaccess` files (`AllowOverride All`,
the usual default on shared hosting). A virtual host, for your own Apache:

```
<VirtualHost *:443>
    ServerName game.example.com
    DocumentRoot /var/www/game
    <Directory /var/www/game>
        AllowOverride All
        Require all granted
    </Directory>
    # SSLEngine on, SSLCertificateFile ... (or certbot --apache)
</VirtualHost>
```

### No domain name

Players then download over plain http from the server's IP address, for example
`http://203.0.113.10/`:

- **Caddy**: begin the block with `:80` instead of `game.example.com`.
- **nginx**: put `listen 80 default_server;` in place of the `server_name` line, remove the default site
  (`sudo rm /etc/nginx/sites-enabled/default`), and skip certbot.
- **Apache**: use `<VirtualHost *:80>`, leave out `ServerName` and the SSL line, and switch the default
  site off (`sudo a2dissite 000-default`).

Over plain http, someone between the server and a player could change what is sent. DeltaPatcher refuses
any file whose SHA-256 is not the one in the manifest; to protect the manifest as well, sign your releases
and give players your public key (see [Sign your releases](PUBLISHING.md#3b-sign-your-releases-optional)).

## Part 2: a key on your PC

In DeltaPatcher, open the **Release** page, pick **Your server (SSH)** under **Where players get it**, and click **New key** in **Set up your server**:

- Keep the suggested file (`deltapatcher_ed25519` in your `.ssh` folder).
- Type a passphrase twice: at least 10 characters (a few words work well). It is not saved anywhere.
- Click **Create key**. The *server line* is copied to your clipboard.

Command line: `deltapatcher ssh keygen`, then `deltapatcher ssh pubkey` prints the server line.

The server line looks like this:

```
restrict,command="internal-sftp" ssh-ed25519 AAAAC3Nza... deltapatcher@your-pc
```

The server has to know this line. The easy way: **Check host key** (Part 3, step 3), then **Put key on
server**: DeltaPatcher logs in once with the deploy user's password, adds the line (never twice, with the
permissions sshd wants) and forgets the password. It works on a Linux server and on Windows' OpenSSH server
(an administrator's key goes to `administrators_authorized_keys` there). Command line:
`deltapatcher ssh install-key`.

Or add it yourself, as one line, to the deploy user's keys on the VPS:

```
sudo nano /home/deploy/.ssh/authorized_keys
```

`restrict,command="internal-sftp"` means this key can only transfer files with SFTP: no shell, no
port forwarding. That is all DeltaPatcher needs. (A server that only takes keys, with passwords switched
off, needs this second way.)

## Part 3: connect and release

On the **Release** page, under **Where players get it**, **Your server (SSH)**:

1. *Host* `game.example.com` (or the IP address), *User* `deploy`, *Port* `22`, *Remote folder*
   `/var/www/game`, and *Players download from* `https://game.example.com/` (without a domain name:
   `http://203.0.113.10/`).
2. **Unlock key** (in **Set up your server**): type the passphrase once. `ssh-agent` keeps the key unlocked until you log out.
   - On Windows the agent service is off by default. In an administrator PowerShell, once:
     ```
     Get-Service ssh-agent | Set-Service -StartupType Automatic
     Start-Service ssh-agent
     ```
3. **Check host key**: compare the fingerprints shown with the ones you wrote down in Part 1. Click **They match: trust this server** only if they do. DeltaPatcher remembers the server, and refuses it later if its key
   ever changes.
4. **Test connection**: the log says `logged in`.
5. Fill in **What to release** (the folder to release) and click **Release**. Turn on **Dry run** first to see what
   would be sent.

Command line, the same steps:

```
deltapatcher ssh unlock
deltapatcher ssh trust --host game.example.com
deltapatcher ssh test --host game.example.com --user deploy --remote-root /var/www/game
deltapatcher config set publish_src=./game ssh_host=game.example.com ssh_user=deploy remote_root=/var/www/game
deltapatcher release --version 1.0
```

The window and the command line share these settings: whatever one saves, the other uses when you
leave it out. After that, every new version is one `deltapatcher release`.

## Part 4: check it from outside

```
curl -I https://game.example.com/manifest.json
```

`200` is good. Players now use `https://game.example.com/` (or `http://203.0.113.10/`) as their source.

## How a deploy keeps players safe

1. It asks the server which blobs it already has, and sends only the missing ones (each one is
   uploaded as `<hash>.part` and renamed when complete).
2. It checks that every blob the new manifest names is on the server, at the right size.
3. It puts the release's signature (when it is signed) and the `.htaccess` in place.
4. Only then does it upload the new manifest, check its size, and switch it into place. Before that
   point a failure changes nothing players see.
5. The release before it stays available as `manifest.previous.json`.
6. It removes blobs and signatures that no manifest on the server names any more. If a manifest on the
   server cannot be read, it removes nothing.

## Windows Server instead of Linux

- Install the OpenSSH Server (in an administrator PowerShell):
  `Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0`, then `Start-Service sshd`.
- For a user in the Administrators group, Windows reads keys from
  `C:\ProgramData\ssh\administrators_authorized_keys`, not the user's own folder. A separate,
  non-admin deploy user is simpler and safer.
- Use forward slashes for the remote folder: `C:/www/game`. If the server says it is not found, try
  `/C:/www/game`.
- If the server does not accept `command="internal-sftp"`, use the line from
  `deltapatcher ssh pubkey --full` (it is limited by `restrict` only).
- Caddy runs on Windows too. With IIS, blobs have no file extension, and IIS refuses to serve unknown
  types: add a `web.config` in `blobs\` with
  `<staticContent><mimeMap fileExtension="." mimeType="application/octet-stream" /></staticContent>`.
  Put the same one in `parts\` (make the folder if it is not there yet): the parts indexes of big archives
  have no extension either. Deploys never remove them.
