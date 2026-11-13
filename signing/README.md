# Code signing

Release builds of `vizier.exe` are Authenticode-signed (SHA-256, RFC 3161 timestamp) with the certificate in this
directory, `vizier-codesign.crt`:

- Subject: `CN=Utopia Models, Inc. (Vizier code signing), O=Utopia Models, Inc., C=US`
- SHA-256 fingerprint: `74:8D:D4:A7:01:D5:83:50:61:A1:0B:AA:00:A4:86:15:44:8C:A0:FF:E1:6D:5C:26:2F:72:2A:13:5E:23:B8:05`
- Valid until 2029-11-11

It is **self-signed** today, so Windows shows the signature as "not trusted" until you trust the certificate. Verify a
download:

```powershell
# Windows: trust our certificate for code signing on this machine (admin), then check the file
Import-Certificate -FilePath vizier-codesign.crt -CertStoreLocation Cert:\LocalMachine\Root
Import-Certificate -FilePath vizier-codesign.crt -CertStoreLocation Cert:\LocalMachine\TrustedPublisher
Get-AuthenticodeSignature .\vizier.exe | Format-List Status, SignerCertificate
```

```bash
# Linux/macOS
osslsigncode verify -CAfile signing/vizier-codesign.crt -in vizier.exe
```

Moving to a publicly trusted certificate (a CA-issued or cloud-signing certificate) changes only the CI secrets and
this file; `scripts/sign.sh` stays the same.

The private key never lives in this repository. CI reads it from the secrets `VIZIER_CODESIGN_PFX_B64` (the PKCS#12,
base64) and `VIZIER_CODESIGN_PFX_PASSWORD`.
