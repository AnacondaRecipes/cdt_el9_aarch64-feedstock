# Rocky Linux 9 CDTs

## Building the CDTs

The following is fairly generic, independent of the (Rocky) Enterprise
Linux variant, `el8`, `el9`, `el10` and the architecture, `x86_64`,
`aarch64`.

The text follows `el8` and `x86_64`, change as required.

The version of GLIBC is Rocky release-specific, `el8` is 2.28, `el9`
is 2.34 etc..

*feedstock-suite* has a
[cdt-build-order.py](https://github.com/anaconda/feedstock-suite/blob/main/bin/cdt-build-order.py)
tool which allows for both local and CI builds.

In both cases, from the feedstock directory:

* `cdt-build-order.py --log=INFO --conda-build` for local builds

* `cdt-build-order.py --log=INFO --pbp` to create `pbp.yaml` for PBP
  builds

  This needs to be run on a platform with the `pbp-cli` installed and
  you can subsequently create the PBP graph from `pbp.yaml`.

There is a legacy script, `conda-build-all` which iterates through the
CDTs building them in order:

``` bash
$ ./cdt_el8_x86_64/conda-build-all [-c {sysroot-staging-channel}]
...
```

>[!TIP]
> The recipes skip anything not `linux-64` so you will want to run
> such a (Docker) instance.

>[!TIP]
> The recipes depend on the linux sysroot using GLIBC 2.28 so you'll
> want to build that first if it is not already published.

``` bash
conda build linux-sysroot-feedstock
```

## Which CDTs

By way of an exercise, were you to search for the use of the CDTs
defined in this repo you might discover five:

* `libselinux*`, `libsepol` and `libxcrypt*`

The remainder are kept for convenience.

## How To Extend The Set of CDTs

Unlike most feedstocks, the CDTs are lots of subdirectories each with
its own `meta.yaml` and `build.sh` -- there is no `recipe` directory
_per se_, each `foo-el8-x86_64` directory is equivalent to another
`recipe` directory.  `conda build` knows what to do with it all --
except figure out the build order.

The basic structure of these files is very simple, you can start by
cut'n'pasting an existing entry noting that `build.sh` is _nearly_
identical in all subdirectories, you'll probably only need to edit
`meta.yaml`:

``` bash
mkdir foo-el8-x86_64
cp libxcrypt-el8-x86_64/* foo-el8-x86_64
```

to fix the package dependences and the `about` section.

### update.py

The script `update.py` will iterate over all `*-el[0-9]+-<arch>`
directories (more specifically, it looks for directories with
`meta.yaml` files) and infers the Rocky package, Enterprise Linux
variant and architecture from the *directory name* and then will

* search the [Rocky
  vault](https://download.rockylinux.org/vault/rocky/) for suitable
  packages, finding the most recent

* rewrite the `meta.yaml` according to the

  * discovered Rocky package, version and build numbers

  * update the GLIBC version

  * edit the dependencies to match the discovered versions

    * [!WARNING] it does not know what the dependencies should be it
      just rewrites what it sees

	  If you are on a Rocky instance (eg. Docker) you can run `rpm`
      over the Rocky packages to discover the package dependencies.
      See below.

  * if you are on a Rocky instance (eg. Docker) it will run `rpm` and
    try to match files in the test section and update them
    accordingly, eg.

	* version changes `libpam.so.0.84.2` to `libpam.so.0.85.1`

	* path changes `.../lib64/libcap-ng.so.0` to
    `.../usr/lib64/libcap-ng.so.0`

Run it as

``` bash
python update.py --log=INFO
```

for some informative updates.

### build.sh

`build.sh` should only be setting up some `lib` / `usr/lib64` symlinks
and copying files from `binary` (where we downloaded and extracted the
RPM into).

To patch some things up, there may be some additional `rm`s in
individual `build.sh` scripts -- so watch out if you're copying a
random one.

### meta.yaml

#### package

`update.py` will perform this section.

Change `name` and `version`!

#### source.url

`update.py` will perform this section.

You would think that most of the entities that we want are in
`BaseOS`,
eg. `https://download.rockylinux.org/vault/rocky/8.9/BaseOS/x86_64/os/Packages`
but some of them might be in `AppStream`,
eg. `https://raw.repo.almalinux.org/vault/8.9/AppStream/x86_64/os/Packages`
and possibly elsewhere.

We obviously want to use the latest version at the time.

##### Source RPM

`update.py` will _delete_ this section!  It was never used (or well
maintained).

Be warned, the source RPM URL, although commented out currently, may
well have a different **name** from the package URL.

#### requirements

`update.py` will make the changes it is aware of (`sysroot` and CDT
dependencies) but does not know what a package's actual dependencies
are.  You'll need to go look in the RPM and edit the `meta.yaml`
accordingly.

The basic section looks like:

``` yaml
requirements:
  build:
    - sysroot_linux-64 2.28.*
  host:
  run:
    - sysroot_linux-64 2.28.*
    - {runtime-dependencies}
```

where `2.28` is the GLIBC version for our chosen Enterprise Linux
release (`el8`, `el9`, etc.) and `{runtime-dependencies}` gets a bit
more interesting.

These are either going to be:

- regular conda dependencies, particularly where we've been building
  out some core dependencies ourselves (so we are less dependent on
  CDTs)

- other CDTs where the dependency information looks like

``` yaml
    - libcap-ng-el8-x86_64 >=0.7.11 *_{{ build_number }}
```

and `0.7.11` is the version number of the CDT we are building (have a
look in `libcap-ng-el8-x86_64/meta.yaml`!) and `*_{{ build_number }}`
marries this recipe up with the same build number across all of these
CDTs -- should we find the need to revisit them.

##### Which Dependencies Are Required?

`update.py` _does not_ perform this section.  Interpreting the `rpm`
output and inferring conda packages or Enterprise Linux packages is a
manual task at this time.

In order to know what dependencies a given CDT has we, essentially,
need to figure out what libraries it uses in turn.

We can query the RPMs `rpm -qp [options] $RPM`.  If you have "built"
the conda package, the source RPM will be available in
`.../conda-bld/src_cache`:

``` bash
$ rpm -qpl $RPM
...file listing...

$ rpm -qpi $RPM
...conda-like package info...

$ rpm -qp --provides pam-1.3.1-27.el8.x86_64_0a6d22f387.rpm
warning: pam-1.3.1-27.el8.x86_64_0a6d22f387.rpm: Header V4 RSA/SHA256 Signature, key ID 6d745a60: NOKEY
config(pam) = 1.3.1-27.el8
libpam.so.0()(64bit)
libpam.so.0(LIBPAM_1.0)(64bit)
libpam.so.0(LIBPAM_EXTENSION_1.0)(64bit)
...

$ rpm -qp --requires pam-1.3.1-27.el8.x86_64_0a6d22f387.rpm
warning: pam-1.3.1-27.el8.x86_64_0a6d22f387.rpm: Header V4 RSA/SHA256 Signature, key ID 6d745a60: NOKEY
...
ld-linux-x86-64.so.2()(64bit)
ld-linux-x86-64.so.2(GLIBC_2.3)(64bit)
libaudit.so.1()(64bit)
libc.so.6()(64bit)
libc.so.6(GLIBC_2.14)(64bit)
libc.so.6(GLIBC_2.15)(64bit)
libc.so.6(GLIBC_2.2.5)(64bit)
libc.so.6(GLIBC_2.27)(64bit)
libc.so.6(GLIBC_2.3)(64bit)
libc.so.6(GLIBC_2.3.4)(64bit)
libc.so.6(GLIBC_2.4)(64bit)
libc.so.6(GLIBC_2.7)(64bit)
libc.so.6(GLIBC_2.8)(64bit)
libc.so.6(GLIBC_2.9)(64bit)
libcrack.so.2()(64bit)
libcrypt.so.1()(64bit)
libcrypt.so.1(XCRYPT_2.0)(64bit)
libdb-5.3.so()(64bit)
libdl.so.2()(64bit)
libdl.so.2(GLIBC_2.2.5)(64bit)
libnsl.so.2()(64bit)
libnsl.so.2(LIBNSL_1.0)(64bit)
...
```

###### Where are those Dependencies?

First of all, query conda because we would prefer to use a regular
conda package over yet another CDT.

We can query whether any existing conda packages have a library
https://conda-metadata-app.streamlit.app/Search_by_file_path?path=lib%2Flibasound.so.2

Otherwise we can search for libraries online:
https://pkgs.org/search/?q=libasound.so.2 and then double check it is
available in https://download.rockylinux.org/vault/rocky/8.9.

The pkgs.org page suggests the Rocky package containing
`libasound.so.2` is `alsa-libs` so would could create an
`alsa-libs-el8-x86_64` directory copying the `meta.yaml`/`build.sh`
files from elsewhere and then let `update.py` figure out the rest.  As
it happens, `alsa-libs` is a conda package (now).

###### Back to Basics

For a given shared library we can run `readelf -d foo.so | grep
NEEDED` and then discover where those libraries are, as above.

Technically, we should do that for any binaries too!

But wait!  I have an RPM, where is the shared library?  And doesn't an
RPM tell us about dependencies?

Well, the RPM's declaration of dependency is for the RPM eco-system
which is instructive but not necessarily fundamentally useful for us.

###### Cross-referencing Dependencies

Of course, one problem, here, is that we don't necessarily know if any
of our existing CDTs contains the library we are looking for.  So what
we want to do is iterate over all of the packages we create and
simultaneously record which libraries appear in which packages (CDTs)
and which libraries each of those libraries require in turn.

A further problem here, is that the success on the first run depends
on which order you probe the packages.  However, if you have recorded
the results of the first pass then on the second pass you should have
a complete set of library->package mappings and therefore you can
successfully match any needed libraries to packages and flag up any
missing ones.

Using such a script,
[cdt-report-library-dependencies](https://github.com/anaconda/feedstock-suite/blob/main/bin/cdt-report-library-dependencies),
in the context of a build we might:

``` bash
$ cd /path/to/aggregate
$ ./cdt_el8_x86_64/conda-build-all [-c {sysroot-staging-channel}]
...
$ cd /path/to/conda-bld/noarch
$ /path/to/cdt-report-library-dependencies *-el8-x86_64*
...
lots of output
...
needed        by                   in
libawt.so     libawt_xawt.so       java-1.8.0-openjdk
libawt.so     libjawt.so           java-1.8.0-openjdk
libdb-5.3.so  pam_userdb.so        pam
libjava.so    libawt_xawt.so       java-1.8.0-openjdk
libjava.so    libjawt.so           java-1.8.0-openjdk
libjava.so    libjsoundalsa.so     java-1.8.0-openjdk
libjvm.so     libawt_xawt.so       java-1.8.0-openjdk
libjvm.so     libjawt.so           java-1.8.0-openjdk
libjvm.so     libjsoundalsa.so     java-1.8.0-openjdk
libnsl.so.2   pam_unix_acct.so     pam
libnsl.so.2   pam_unix_auth.so     pam
libnsl.so.2   pam_unix_passwd.so   pam
libnsl.so.2   pam_unix_session.so  pam
libnsl.so.2   pam_unix.so          pam
```

Here, we might debate about whether it matters that we are missing
these libraries.

##### -devel packages

If we are looking at some RPM, `foo`, and there is a corresponding
`foo-devel` RPM then we should be including both `foo-devel` and `foo`
RPM in our set of CDTs.

#### about section

`update.py` can partially perform this section (if `rpm` is available,
ie. on an Enterprise Linux box).

You can script something that extracts what Rocky thinks the package
metadata is, package URL, summary, description licensing with the
likes of:

``` bash
rpm -qpi ${rpm}
```

and filtering the results.

Note that the licensing is in a Rocky preferred format and does not
necessarily reflect the conda SPDX format.  Also, it might be worth
reading around the upstream package for interpretation of the "or
later" clause which tends to be inferred.

Given that you may have previously discerned some more accurate
licensing `update.py` will try to preserve any existing license
statements and note the Rocky license statement in a comment if it is
different.

Better, however, is being able to discover the actual license files:

``` bash
rpm -qpL ${rpm} 2>/dev/null | grep /
```

which you can extract with:

``` bash
rpm2cpio ${rpm} | cpio --directory {pkg_dir} -idv .${file}
mv ${file} {pkg_dir}
```

Note that the extracted `file` will be a full pathname,
`/usr/share/path/to/LICENSE` and we simply want `LICENSE` in the
package's recipe directory, here `{pkg_dir}`.

`update.py` will rewrite the `about` section so if you have some
comments explaining details about the licensing, say, then put them
before the `about` section.

## How To Make a New Set of CDTs

To make a new set, eg. an Enterprise Linux release, `el8` to `el9`, or
an architecture change, `aarch64` to `x86_64` then we should be able
to copy the tree, rename the sub directories and then `update.py`
should take care of the rest.

### New Architecture

``` bash
mkdir cdt_el9_x86_64
cp cdt_el9_aarch64/{conda-build-all,update.py,README.md} cdt_el9_x86_64
(cd cdt_el9_aarch64; tar cf - *) | (cd cdt_el9_x86_64; tar xf -)
cd cdt_el9_x86_64
for x in *-aarch64; do mv $x ${x/aarch64/x86_64} ; done
python update.py --log=INFO
```
