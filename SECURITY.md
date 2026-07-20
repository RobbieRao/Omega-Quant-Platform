# Security policy

## Supported versions

Security fixes are applied to the latest tagged beta or stable release and the
default branch.

## Report a vulnerability

Please use GitHub's private vulnerability reporting for this repository. Do not
open a public issue for credential exposure, authentication bypass, unsafe order
routing, account disclosure, or dependency compromise.

Include the affected revision, reproducible steps using synthetic/fake data,
impact, and a suggested mitigation if known. Do not attempt a real broker order,
access another person's account, or retain private financial data.

## Credential response

If a real TigerOpen or Massive credential is exposed, stop the release, revoke
or rotate it at the vendor, remove it from the public artifact and history, and
rerun the full secret scan. Deleting only the latest file is not sufficient.
