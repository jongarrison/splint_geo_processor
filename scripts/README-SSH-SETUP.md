# SSH Setup for Windows (Splint Geo Processor)

This guide helps you set up SSH access to your Windows machine running the Splint Geo Processor.

## Quick Setup

### On Windows Machine:

1. **Open PowerShell as Administrator**
   - Press `Win + X`
   - Select "Windows PowerShell (Admin)" or "Terminal (Admin)"

2. **Run the setup script**
   ```powershell
   cd path\to\splint_geo_processor\scripts
   .\setup-windows-ssh.ps1
   ```

3. **Note your IP address** (displayed at end of script)
   - Or run: `ipconfig` and look for IPv4 Address

4. **Add your SSH public key**
   - The script creates: `C:\Users\YourUsername\.ssh\authorized_keys`
   - Copy your public key from Mac (step below) and paste it into this file
   - Open with: `notepad $env:USERPROFILE\.ssh\authorized_keys`

### On Mac:

1. **Generate SSH key** (if you don't have one)
   ```bash
   ssh-keygen -t ed25519 -C "your_email@example.com"
   # Press Enter to accept defaults
   # Set a passphrase or leave empty
   ```

2. **Copy your public key**
   ```bash
   cat ~/.ssh/id_ed25519.pub
   ```
   Copy the entire output (starts with `ssh-ed25519`)

3. **Add Windows machine to SSH config** (optional but convenient)
   ```bash
   cat >> ~/.ssh/config << 'EOF'
   Host splint-geo-win
       HostName 192.168.1.XXX  # Replace with your Windows IP
       User YourWindowsUsername
       IdentityFile ~/.ssh/id_ed25519
       ServerAliveInterval 60
   EOF
   ```

4. **Test the connection**
   ```bash
   # Using IP directly:
   ssh YourWindowsUsername@192.168.1.XXX

   # Or using the config alias:
   ssh splint-geo-win
   ```

## Useful Commands

### From Mac to Windows:

```bash
# Copy file to Windows
scp file.txt splint-geo-win:C:/Users/YourUser/

# Copy directory to Windows
scp -r ./directory splint-geo-win:C:/Users/YourUser/

# Run PowerShell command
ssh splint-geo-win "powershell.exe -Command Get-Process"

# Interactive PowerShell session
ssh splint-geo-win
```

### On Windows (PowerShell):

```powershell
# Check SSH service status
Get-Service sshd

# Restart SSH service
Restart-Service sshd

# View SSH logs
Get-Content C:\ProgramData\ssh\logs\sshd.log -Tail 50

# Test SSH locally
ssh localhost

# View authorized keys
notepad $env:USERPROFILE\.ssh\authorized_keys

# Check firewall rules
Get-NetFirewallRule -Name *ssh*
```

## Troubleshooting

### Can't connect from Mac:

1. **Check Windows firewall**
   ```powershell
   # On Windows:
   Get-NetFirewallRule -Name *ssh* | Select-Object DisplayName, Enabled
   ```

2. **Verify SSH service is running**
   ```powershell
   # On Windows:
   Get-Service sshd
   ```

3. **Check Windows IP address**
   ```powershell
   # On Windows:
   ipconfig
   ```

4. **Test connection with verbose output**
   ```bash
   # On Mac:
   ssh -vvv YourWindowsUsername@windows-ip
   ```

### "Permission denied (publickey)" error:

1. **Check authorized_keys permissions**
   ```powershell
   # On Windows, run as Administrator:
   icacls "$env:USERPROFILE\.ssh\authorized_keys"
   # Should show: YourUsername:(R) and SYSTEM:(F)
   ```

2. **Fix permissions if needed**
   ```powershell
   # On Windows, run as Administrator:
   icacls "$env:USERPROFILE\.ssh\authorized_keys" /inheritance:r
   icacls "$env:USERPROFILE\.ssh\authorized_keys" /grant:r "${env:USERNAME}:(R)"
   icacls "$env:USERPROFILE\.ssh\authorized_keys" /grant:r "SYSTEM:(F)"
   ```

3. **Verify public key format**
   - Open `authorized_keys` on Windows
   - Should be one line starting with `ssh-ed25519` or `ssh-rsa`
   - No extra spaces or line breaks

### "Connection timed out" error:

1. **Check if both machines are on same network**
2. **Verify Windows firewall allows SSH (port 22)**
3. **Try pinging Windows from Mac**: `ping windows-ip`

## Security Hardening (Optional)

After confirming SSH key authentication works:

1. **Disable password authentication**
   ```powershell
   # On Windows, edit sshd_config:
   notepad C:\ProgramData\ssh\sshd_config
   
   # Change this line:
   PasswordAuthentication no
   
   # Save and restart SSH:
   Restart-Service sshd
   ```

2. **Change default SSH port** (optional)
   ```powershell
   # Edit sshd_config:
   notepad C:\ProgramData\ssh\sshd_config
   
   # Change:
   Port 2222
   
   # Update firewall:
   New-NetFirewallRule -Name sshd-custom -DisplayName 'OpenSSH Custom Port' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 2222
   
   # Restart SSH:
   Restart-Service sshd
   
   # Connect with custom port from Mac:
   ssh -p 2222 splint-geo-win
   ```

## What the Script Does

The `setup-windows-ssh.ps1` script:
- ✓ Installs OpenSSH Server if not present
- ✓ Configures SSH service to start automatically
- ✓ Adds Windows Firewall rule for SSH
- ✓ Creates Splint Factory working directories
- ✓ Optimizes SSH server configuration
- ✓ Creates .ssh directory and authorized_keys file
- ✓ Sets correct permissions on authorized_keys
- ✓ Displays network information for easy connection

## Next Steps After SSH Setup

Once SSH is working:

1. **Install Node.js on Windows**
   ```powershell
   winget install OpenJS.NodeJS.LTS
   ```

2. **Clone or copy the splint_geo_processor project**
   ```bash
   # From Mac:
   scp -r /path/to/splint_geo_processor splint-geo-win:C:/Users/YourUser/
   ```

3. **Install dependencies**
   ```powershell
   # On Windows via SSH:
   cd C:\Users\YourUser\splint_geo_processor
   npm install
   ```

4. **Configure secrets and environment variables**
   ```powershell
   # Create secrets directory and config file
   mkdir secrets
   notepad secrets\config.json
   ```
