# Android release signing

The production package is signed with the dedicated MoonCen release key. Keep using this same key for every future update.

- Keystore: `C:\Users\gen1w\.android\mooncen-monitor-release.p12`
- Password envelope: `C:\Users\gen1w\.android\mooncen-monitor-release.pass.dpapi`
- Alias: `mooncen-monitor`
- Store type: PKCS12
- Certificate SHA-256: `c9f655472d1ff4ead58be4e6bb2203bca1bd603cf8cc646798ead038c7cd58ee`
- DPAPI scope: CurrentUser (`GEN1WIN\gen1w`)
- DPAPI entropy label: `MoonCenMonitorReleaseSigning-v1`

The password envelope is machine/profile-bound operational storage, not a portable backup. Store an offline copy of the P12 and its password in separate secured locations, and test restoration. Never commit the keystore, password envelope, password, or `keystore.properties`.

The legacy online APK used a different certificate. Version 1.8.0 is therefore a one-time signing-key transition: users must uninstall the legacy app, reinstall the APK, and re-enter the API URL and token. In-place updates from version 1.7.0 or earlier are not possible.

Current signed artifact:

- Artifact: `app/build/outputs/apk/release/mooncen-monitor-1.8.4.apk`
- Version: `1.8.4` (`versionCode` 13)
- Size: `90784` bytes
- APK SHA-256: `0bafe7727598e9b25c6b2ff253f84fd7a61ad37d27973edc821a18c4c44b63ee`
- Signature: APK Signature Scheme v2 and v3 verified

Public deployment verification completed at `2026-08-12T14:28:03Z`: a fresh
download matched the recorded size and SHA-256, reported version `1.8.4` /
code `13`, passed `zipalign`, and verified with APK Signature Scheme v2 and v3
using the certificate above. The server-side rollback point is
`/opt/mooncen-backups/20260812T142039Z-apk-crawler-monitoring`.

Release order:

1. Build the unsigned release APK.
2. Run `zipalign` before signing.
3. Sign with APK Signature Scheme v2 and v3.
4. Verify alignment, package/version metadata, APK signature, certificate fingerprint, and SHA-256.
5. Publish the APK first and `latest.json` last.
6. Download the public APK again and repeat all verification checks.
