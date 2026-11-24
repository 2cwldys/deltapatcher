// The Release page's "Set up your server": the one-time steps (the SSH key: made, on the server, unlocked; the
// server's host key; the signing key), and the line that says where they stand. The part folds by itself once
// everything in it is done. Nothing here uses the window, so a test can run it as it is.

// what the engine says of the SSH key and the host (ssh status)
export interface SshStatus {
  key: string
  key_exists: boolean
  has_passphrase?: boolean
  permission_problems?: string[]
  agent: 'keys' | 'empty' | 'none'
  in_agent?: boolean
  host?: string
  host_trusted?: boolean
  host_error?: string
}

// what the engine says of the signing key (sign status)
export interface SignStatus {
  path: string
  exists: boolean
  has_passphrase: boolean
  can_sign: boolean
}

export interface SetupState {
  // everything is done: the part starts folded
  ready: boolean
  // the line shown with it, folded or not
  line: string
}

// The server's steps: a key, nobody else can read it, it is unlocked (or needs no passphrase), the host is trusted.
// Signing is optional: with sign on, the signing key must be there too. `ssh` null: not read yet.
export function serverSetup(ssh: SshStatus | null, sign: boolean, signing: SignStatus | null): SetupState {
  if (!ssh) return { ready: false, line: 'Reading the state of the key and the server...' }
  const signed = sign ? 'releases signed' : 'releases not signed'
  if (ssh.host_error) return { ready: false, line: `Not ready: ${ssh.host_error}` }
  if (!ssh.key_exists) return { ready: false, line: 'Not ready: no SSH key yet (New key)' }
  if ((ssh.permission_problems ?? []).length) return { ready: false, line: 'Not ready: the key is readable by others (Fix permissions)' }
  if (ssh.has_passphrase && !ssh.in_agent) return { ready: false, line: 'Not ready: the key is locked (Unlock key)' }
  if (!ssh.host) return { ready: false, line: 'Not ready: no host yet (Where players get it)' }
  if (!ssh.host_trusted) return { ready: false, line: 'Not ready: the host key is not confirmed yet (Check host key)' }
  if (sign && !signing?.exists) return { ready: false, line: 'Not ready: signing is on and there is no signing key (New signing key)' }
  const key = ssh.has_passphrase ? 'key unlocked' : 'key without a passphrase'
  return { ready: true, line: `Ready: ${key}, host trusted, ${signed}` }
}

// Signing alone (a release copied to a folder, or served on this computer): done once releases are signed.
export function signingSetup(sign: boolean, signing: SignStatus | null): SetupState {
  if (!sign) return { ready: false, line: 'Releases are not signed' }
  if (!signing) return { ready: false, line: 'Reading the signing key...' }
  if (!signing.exists) return { ready: false, line: 'Signing is on and there is no signing key yet (New signing key)' }
  return { ready: true, line: `Releases signed with your key${signing.has_passphrase ? ', passphrase protected' : ''}` }
}
