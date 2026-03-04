"""run this in feedstock root like
python update.py
or
python update.py -log=INFO

An extension of the sysroot update.py script.

Broadly, look in the Rocky vault and update the recipes' version
numbers including the inter-recipe cross-reference versions.

The directory name defines the package's {name}-{EL}-{arch} for
rewriting the meta.yaml and build.sh files.  This allows for, by
design, the copying and rename of *-aarch64 to *-x86_64 or *-el8-* to
*-el9-* and the right thing should happen.

The rocky_el2ver function defines which Rocky release to use for el8,
el9 etc..

ToDo:

- handle rocky/pub as well as rocky/vault

- handle multiple elN/arch (requires extra levels of indirection for
  stashing results)

"""
import argparse
import hashlib
import logging
import os
import re
import requests
import sys
import tempfile
from ruamel.yaml import YAML
from packaging.version import Version
from pathlib import Path
from subprocess import check_output, CalledProcessError, DEVNULL, Popen, PIPE

parser = argparse.ArgumentParser()
parser.add_argument('-log', '--loglevel', default='warning')

args = parser.parse_args()
logging.basicConfig(level=args.loglevel.upper())

try:
    # saves a bit of bandwidth when testing
    import requests_cache
    session = requests_cache.CachedSession('requests_cache')
except:
    session = requests.Session()

have_rpm = True

try:
    # rpm without args exits non-zero
    check_output(['rpm', '--help'], stderr=DEVNULL)
except (CalledProcessError, FileNotFoundError):
    have_rpm = False

yaml = YAML(typ='rt')

# I don't like this but some replacements just need to know that the
# (possible) archs are
ROCKY_ARCHS = [
    'x86_64',
    'aarch64',
]

ROCKY_ARCHS_RE = '(' + '|'.join(ROCKY_ARCHS) + ')'

# and we need to map Rocky archs to conda archs
ROCKY_CONDA_ARCH = {
    'x86_64': '64',
    'aarch64': 'aarch64',
}

JAVA_ARCHS = [
    'x86_64',
    'arm64',
]

JAVA_ARCHS_RE = '(' + '|'.join(JAVA_ARCHS) + ')'

# and we need to map Rocky archs to Java archs
ROCKY_JAVA_ARCH = {
    'x86_64': 'x86_64',
    'aarch64': 'arm64',
}

# The Rocky release we use per Enterprise Linux version might change
# over time
ROCKY_EL_RELEASES = {
    'el8': '8.9',
    'el9': '9.5',
    'el10': '10.0',
}

def rocky_el2ver(rocky_el:str):
    if rocky_el in ROCKY_EL_RELEASES:
        return ROCKY_EL_RELEASES[rocky_el]
    else:
        logging.error(f"rocky_el2ver: unexpected Rocky release: {rocky_el}")
        sys.exit(1)

# The GLIBC version per Enterprise Linux version should never change
ROCKY_EL_GLIBC = {
    'el8': '2.28',
    'el9': '2.34',
    'el10': '2.39',
}

def rocky_el2glibc(rocky_el:str):
    if rocky_el in ROCKY_EL_GLIBC:
        return ROCKY_EL_GLIBC[rocky_el]
    else:
        logging.error(f"rocky_el2glibc: unexpected Rocky release: {rocky_el}")
        sys.exit(1)

def rewrite_build(my:str):
    mo = re.match(rf"(.+)/meta.yaml", my)
    if not mo:
        logging.warning(f"Could not match against {my} for .+/meta.yaml")
        return

    build_sh = f"{mo.group(1)}/build.sh"

    new_build = []

    with open(build_sh) as f:
        old_build = f.readlines()

    for line in old_build:
        line = line.rstrip()

        # *-el8-*
        mo = re.match(rf"^(.*)_el[0-9]+-(.*)", line)

        if mo:
            line = f"{mo.group(1)}_{rocky_el}-{mo.group(2)}"

        # */{arch}-conda*
        mo = re.match(r"^(.*\${PREFIX}/)(([^-]+)-conda)(.*)", line)

        if mo:
            line = f"{mo.group(1)}{rocky_arch}-conda{mo.group(4)}"

        # Java uses different archs to Rocky...  Here where we need to
        # rewrite some symlinks
        mo = re.match(rf"^(.*/){JAVA_ARCHS_RE}(/.*)", line)

        if mo:
            java_arch = ROCKY_JAVA_ARCH[rocky_arch]
            line = f"{mo.group(1)}{java_arch}{mo.group(3)}"

        new_build.append(line)

    new_build.append("")

    with open(build_sh, "w") as f:
        f.write("\n".join(new_build))


def recreate_about():
    # A putative about section -- we generate it after we've consumed
    # the old one because we want to preserve conda licensing data in
    # preference to Red Hat licensing data
    new_meta.append("about:")
    URL = False
    if 'URL' in pkg_data[pkg]['rpm_info']:
        URL = pkg_data[pkg]['rpm_info']['URL']
    if URL:
        new_meta.append(f"  home: {URL}")
    if 'License' in pkg_data[pkg]['rpm_info']:
        new_meta.append(f"  license: {pkg_data[pkg]['rpm_info']['License']}")

        if 'license_family' in pkg_data[pkg]['rpm_info']:
            new_meta.append(f"  license_family: {pkg_data[pkg]['rpm_info']['license_family']}")

        # -devel packages can use the non-devel license files if they need to
        am_devel = False
        lpkg = pkg
        have_license_files = False
        for key in ['rpm_license_files', 'license_files']:
            lfiles = pkg_data[lpkg][key]
            if len(lfiles) > 0:
                have_license_files = True
                break

        pkg_mo = re.match(r"^(.+)-devel$", pkg)
        if pkg_mo:
            am_devel = True
            lpkg = pkg_mo.group(1)

        if not have_license_files:
            if am_devel:
                # Slightly inefficient to loop over again...
                for key in ['rpm_license_files', 'license_files']:
                    lfiles = pkg_data[lpkg][key]
                    if len(lfiles) > 0:
                        have_license_files = True
                        break

        if have_license_files:
            new_meta.append(f"  license_file:")
            for lf in lfiles:
                lf_mo = re.match(r".*?([^/]+)$", lf)
                if lf_mo:
                    lic_basename = lf_mo.group(1)
                    new_meta.append(f"    - {lic_basename}")

                    # check the file is there
                    pkg_dir = list(Path('.').glob(f'{pkg}-{rocky_el}-{rocky_arch}'))[0]
                    conda_lic_files = list(Path('.').glob(f'{pkg_dir}/{lic_basename}'))
                    if len(conda_lic_files) == 0 or not str(conda_lic_files[0]).endswith(f"/{lic_basename}"):
                        # oops
                        if am_devel:
                            lpkg_dir = list(Path('.').glob(f'{lpkg}-{rocky_el}-{rocky_arch}'))[0]
                            conda_lic_files = list(Path('.').glob(f'{lpkg_dir}/{lic_basename}'))
                            if len(conda_lic_files) > 0:
                                logging.info(f"{pkg:25} copying {conda_lic_files[0]} to {pkg}")
                                check_output(['cp', conda_lic_files[0], pkg_dir])
                            else:
                                logging.warning(f"{pkg:25} unable to copy {lic_basename} for {pkg}")
                        else:
                            logging.warning(f"{pkg:25} unable to create {lic_basename} for {pkg}")
                            logging.warning(f"{pkg:25} not devel? {lpkg}")


    if 'Summary' in pkg_data[pkg]['rpm_info']:
        new_meta.append(f"  summary: (CDT) {pkg_data[pkg]['rpm_info']['Summary']}")
    if 'Description' in pkg_data[pkg]['rpm_info']:
        new_meta.append("  description: |")
        for desc_line in pkg_data[pkg]['rpm_info']['Description']:
            if len(desc_line) > 0:
                new_meta.append(f"    {desc_line}")
            else:
                new_meta.append("")
    if URL:
        new_meta.append(f"  doc_url: {URL}")
        new_meta.append(f"  dev_url: {URL}")

    new_meta.append("")


# Discover all the package names => package {letter}s => html pages
# from url_template => multiple versions => most recent version =>
# source.url and checksum

# just the subdirs with meta.yaml
meta_yamls = list(Path('.').glob('*/meta.yaml'))

pkgs = []
letters = set()
for my in meta_yamls:
    # java-1.8.0-openjdk-devel-el9-aarch64/meta.yaml
    mo = re.match(rf"(.+(-devel)?)-(el[0-9]+)-([a-z0-9_]+)/meta.yaml", str(my))
    if not mo:
        logging.warning(f"Could not match against {my} for el[0-9]+-<arch>/meta.yaml")
        continue

    pkg = mo.group(1)

    rocky_el = mo.group(3)
    rocky_arch = mo.group(4)

    rocky_version = rocky_el2ver(rocky_el)
    glibc_version = rocky_el2glibc(rocky_el)

    pkgs.append(pkg)
    letters.add(str(my)[0])

url_template = (
    f"https://download.rockylinux.org/vault/rocky/{rocky_version}"
    # second part intentionally not filled yet
    "/{subfolder}/{arch}/os/Packages/{letter}"
)

el_ver = "el" + rocky_version.replace(".", "_")

# We'll assume that all architectures are the same as whichever
# rocky_arch was left from the above loop whilst we do some background
# shuffling and then do a per-arch setting for each yaml file
#
# If you have mixed rocky releases, eg. el8 and el9, in the same
# directory then this code needs tweaking.

# We have to look in several subfolders for some things which means
# we'll get duplicate entries for others -- hopefully they are not in
# conflict!
rocky_subfolders = [
    "BaseOS",
    "AppStream",
    "devel",      # 9+
    "PowerTools"  # 8.9
]
pkg_pages = []
for sf in rocky_subfolders:
    for letter in letters:
        pkg_pages.append(url_template.format(subfolder=sf, arch=rocky_arch, letter=letter))

page_html = ""
for page in pkg_pages:
    logging.debug(f"Fetching content of {page}")
    page_html += session.get(page).content.decode("utf-8")

# Get content of https://download.rockylinux.org/vault/rocky/9.5/BaseOS/x86_64/os/Packages/g/,
# which looks something like:
# ```
# <html>
# <head><title>Index of /vault/9.5/BaseOS/x86_64/os/Packages/</title></head>
# <body>
# <h1>Index of /vault/9.5/BaseOS/x86_64/os/Packages/</h1>
# <table ...>
# <tr><td ...><a href="{pkg}-{string}.x86_64.rpm" title="...">{pkg}-{string}.x86_64.rpm</a></td>... {size} {date}</tr>
# ...
# </table>
# ...
# ```

# We'll generate a table of version,build1,build2 tuples where we can
# pick the most recent -- this probably applies less to regular
# packages but (for the sysroot) you'll get several builds of glibc
# etc..  FWIW I can see a couple of builds of pam.
pkg_data = {}
for line in page_html.splitlines():
    line = line.strip()
    mo = re.match(r".*<a href=\"([^\"]+)\" title=.*", line)
    if not mo:
        #logging.warning(f"Could not match against {line} for href")
        continue
    url = mo.group(1)

    if rocky_el not in url:
        logging.warning(f"Could not match against {url} for {rocky_el}")
        continue

    skip = False
    if not url.endswith(f"{rocky_arch}.rpm"):
        if url.startswith(f"javapackages-filesystem") or url.startswith(f"copy-jdk-configs"):
            pass
        elif url.endswith(f".noarch.rpm"):
            skip = True
        else:
            if rocky_arch == "x86_64" and re.match(rf"^.*\.{rocky_el}.*\.i686\.rpm$", url):
                # lots of warnings!
                continue
            else:
                logging.warning(f"Could not match against {url} for {rocky_arch}")
            skip = True

    if skip:
        continue

    # The ones we want look like: libcap-ng-0.8.2-7.el9.aarch64.rpm --
    # there are other formats
    mo = re.match(rf"([^/]+)-([.0-9]+)-([.0-9]+)\.{rocky_el}(_([.0-9]+))?\.{rocky_arch}\.rpm$", url)
    if not mo:
        # some special cases:
        # javapackages-filesystem-6.0.0-7.el9_5.noarch.rpm
        mo = re.match(rf"([^/]+)-([.0-9]+)-([.0-9]+)\.{rocky_el}(_([.0-9]+))?\.noarch\.rpm$", url)

        if not mo:
            # java-1.8.0-openjdk-1.8.0.452.b09-2.el9.aarch64.rpm
            mo = re.match(rf"([^/]+)-([.0-9b]+)-([.0-9]+)\.{rocky_el}(_([.0-9]+))?\.{rocky_arch}\.rpm$", url)

        if not mo:
            # logging.warning(f"Could not split {url}")
            continue

    if mo.group(1) in pkgs:
        pkg=mo.group(1)
        ver=mo.group(2)
        build1=mo.group(3)
        if mo.group(5):
            build2=mo.group(5)
        else:
            build2="0"

        ver_v=Version(ver)
        build1_v=Version(build1)
        build2_v=Version(build2)

        set_me=False
        if pkg in pkg_data:
            if ver_v > pkg_data[pkg]['ver']:
                logging.info(f"{pkg:25} bump {pkg_data[pkg]['ver']} -> {ver_v}")
                set_me=True
            elif build1_v > pkg_data[pkg]['build1']:
                logging.info(f"{pkg:25} bump {pkg_data[pkg]['ver']} {pkg_data[pkg]['build1']} -> {build1_v}")
                set_me=True
            elif build2_v > pkg_data[pkg]['build2']:
                logging.info(f"{pkg:25} bump {pkg_data[pkg]['ver']} {pkg_data[pkg]['build1']} {pkg_data[pkg]['build2']} -> {build2_v}")
                set_me=True
        else:
            set_me=True

        if set_me:
            pkg_data[pkg] = {
                'pkg': pkg,
                'ver': ver_v,
                'build1': build1_v,
                'build2': build2_v,
                'base_url': url,
                'url': "url",
                'sha': "sha",
                'rpm_data': "rpm",
                'rpm_files': [],
                'rpm_info': {},
                'rpm_license_files': [],
                'license_files': [], # manual discovery
            }

fail = False
for pkg in pkgs:
    if not pkg in pkg_data:
        logging.warning(f"Failed to discover {pkg}")
        fail = True

if fail:
    logging.error("Cannot continue without package data")
    sys.exit(1)

name2rpm = {
    # package name to rpm
}

for pkg in pkg_data:
    name2rpm[pkg] = pkg_data[pkg]['base_url']

def get_subfolder(pkg, rpm):
    pkg_template = url_template + f"/{rpm}"
    for sf in rocky_subfolders:
        url = pkg_template.format(arch=rocky_arch, subfolder=sf, letter=pkg[0])
        logging.debug(f"Testing if {url} exists")
        if session.get(url).status_code == 200:
            return sf
    raise ValueError(f"could not find valid artefact for {pkg} {rpm}!")

for pkg, rpm in name2rpm.items():
    subfolder = get_subfolder(pkg, rpm)

    rpm_url = (
        url_template.format(arch=rocky_arch, subfolder=subfolder, letter=pkg[0])
        + f"/{rpm}"
    )

    logging.debug(f"Downloading {rpm_url}")
    r = session.get(rpm_url)
    if r.status_code != 200:
        logging.warning(f"{r.status_code}: Could not download rpm for {pkg} from {rpm_url}!")
        continue
    sha = hashlib.sha256(r.content).hexdigest();

    pkg_data[pkg]['url'] = rpm_url
    pkg_data[pkg]['sha'] = sha
    pkg_data[pkg]['rpm_data'] = r.content

    if have_rpm:
        # Let's have a look in the rpm for some useful data

        with tempfile.NamedTemporaryFile(suffix=".rpm",
                                         delete_on_close=False) as fp:
            fp.write(pkg_data[pkg]['rpm_data'])
            fp.close()

            output = check_output(['rpm', '-ql', fp.name], stderr=DEVNULL)
            for rpm_line in output.splitlines():
                rpm_line = rpm_line.rstrip().decode('utf-8')
                pkg_data[pkg]['rpm_files'].append(rpm_line)

            output = check_output(['rpm', '-qL', fp.name], stderr=DEVNULL)
            for rpm_line in output.splitlines():
                rpm_line = rpm_line.rstrip().decode('utf-8')
                pkg_data[pkg]['rpm_license_files'].append(rpm_line)

                lic_basename = rpm_line
                lic_mo = re.match(r".*/([^/]+)$", rpm_line)
                if lic_mo:
                    lic_basename = lic_mo.group(1)

                # Extract the license file
                pkg_dir = list(Path('.').glob(f'{pkg}-{rocky_el}-{rocky_arch}'))[0]

                conda_lic_files = list(Path('.').glob(f'{pkg_dir}/{lic_basename}'))
                if len(conda_lic_files) == 0:
                    logging.info(f"{pkg:25} extracting license file {lic_basename}")
                    rpm2cpio = Popen(['rpm2cpio', fp.name], stdout=PIPE)
                    output = check_output(['cpio',
                                           '--directory',
                                           pkg_dir,
                                           '-idv',
                                           f".{rpm_line}"
                                           ],
                                          stdin=rpm2cpio.stdout)
                    rpm2cpio.wait()

                    # rename {pkg_dir}/usr/share/.../LICENSE to
                    # {pkg_dir}/LICENSE
                    os.rename(f"{pkg_dir}{rpm_line}", f"{pkg_dir}/{lic_basename}")

            output = check_output(['rpm', '-qi', fp.name], stderr=DEVNULL)
            in_description = False
            for rpm_line in output.splitlines():
                rpm_line = rpm_line.rstrip().decode('utf-8')

                if in_description:
                    if 'Description' not in pkg_data[pkg]['rpm_info']:
                        pkg_data[pkg]['rpm_info']['Description'] = []
                    pkg_data[pkg]['rpm_info']['Description'].append(rpm_line)
                else:
                    # Grr the ID can have trailing spaces, embedded
                    # spaces or no trailing space (and combinations)
                    info_mo = re.match(r"^([^:]+):\s*(.*)", rpm_line)
                    if info_mo:
                        info_id = info_mo.group(1).rstrip()
                        if info_id == 'Description':
                            in_description = True
                            continue
                        if info_id in ['URL', 'License', 'Summary']:
                            pkg_data[pkg]['rpm_info'][info_id] = info_mo.group(2)
                    else:
                        logging.warning(f"{pkg:25} Unexpected info line: {rpm_line}")


# these want to have 7 capture groups because... it makes life easier
# leader        group(1)        - initial whitespace
# prefix        group(2)        - up to elN or path
# optional      group(3)        - invert (should it exist or not)
# complete_suffix group(4)      - rest of line
# suffix_leader group(5)        - up to incl. /sysroot
# test_path     group(6)        - RPM path-ish, eg. /lib64/foo.so.0
# test_file     group(7)        - eg. foo.so.0
test_res = {
    r"^(\s+)- (test( !)? -f .*)_el[0-9]+-((linux.*/sysroot)(.+/([^/]+)))$": "{leader}- {prefix}_{rocky_el}-{complete_suffix}",
    r"^(\s+)- (test( !)? -f .*)-((linux.*/sysroot)(.+/([^/]+)))$": "{leader}- {prefix}-{complete_suffix}",
    # other el[0-9] paths, eg. export ${PREFIX}/aarch64-conda_el8-linux-gnu/sysroot/...
    r"^(\s+)- ((.).*)_el[0-9]+-((linux.*/sysroot)(.+/([^/]+)))$": "{leader}- {prefix}_{rocky_el}-{complete_suffix}",
}

rpm_warning = False
for my in meta_yamls:
    mo = re.match(rf"(.+(-devel)?)-(el[0-9]+)-([a-z0-9_]+)/meta.yaml", str(my))
    if not mo:
        logging.warning(f"Could not match against {my} for el[0-9]+-<arch>/meta.yaml")
        continue

    pkg = mo.group(1)

    if not pkg in pkg_data:
        continue

    rocky_el = mo.group(3)
    rocky_arch = mo.group(4)

    rocky_version = rocky_el2ver(rocky_el)
    glibc_version = rocky_el2glibc(rocky_el)

    data = pkg_data[pkg]
    logging.info(f"{pkg:25} {str(data['ver']):12} from {my}")

    new_meta = []

    with open(my) as f:
        old_meta = f.readlines()

    test_id = 0
    skip = False
    recreated_about = False
    in_license_files = False
    for line in old_meta:
        line = line.rstrip()
        if line.startswith("  name:"):
            line = f'  name: {pkg}-{rocky_el}-{rocky_arch}'
        elif line.startswith("  version"):
            line = f'  version: {data["ver"]}'
        elif line.startswith("  - url:"):
            line = f'  - url: {data["url"]}'
        elif line.startswith("    sha256:"):
            line = f'    sha256: {data["sha"]}'
        elif line.startswith(f"  skip: True"):
            line = f'  skip: True  # [not (linux and {rocky_arch})]'
        elif line.startswith(f"    - sysroot_linux-"):
            conda_arch = ROCKY_CONDA_ARCH[rocky_arch]
            line = f'    - sysroot_linux-{conda_arch} {glibc_version}.*'
        elif line.startswith("  number: 0"):
            line = f'  number: {{{{ build_number }}}}'
        elif line.startswith("  # - url:") or line.startswith("  #   no_hoist:") or line.startswith("  #   folder:"):
            # clean up unused elements
            skip = True
        elif line.startswith("extra:"):
            # finish consuming the about section
            skip = False

            recreate_about()
            recreated_about = True

        elif line.startswith("about:"):
            # consume the existing about section up to extras
            skip = True

        # However, grab the existing licensing as it is a lot of
        # work to restore
        if skip:
            lic_mo = re.match(r"  license:\s*([^#]+)", line)
            if lic_mo:
                lic = lic_mo.group(1).rstrip()
                # license: <SPDX>  # Red Hat license if different
                if 'License' in pkg_data[pkg]['rpm_info'] and lic != pkg_data[pkg]['rpm_info']['License']:
                    pkg_data[pkg]['rpm_info']['License'] = f"{lic}  # {pkg_data[pkg]['rpm_info']['License']}"
                else:
                    pkg_data[pkg]['rpm_info']['License'] = f"{lic}"
            lic_mo = re.match(r"  license_family:\s*(.*)", line)
            if lic_mo:
                pkg_data[pkg]['rpm_info']['license_family'] = lic_mo.group(1)
            if line.startswith("  license_file:"):
                in_license_files = True
            elif in_license_files:
                lic_mo = re.match(r"\s+-\s+(.*)", line)
                if lic_mo:
                    pkg_data[pkg]['license_files'].append(lic_mo.group(1))
                else:
                    in_license_files = False

        if skip:
            continue

        # build/host/run dependency lines
        mo = re.match(rf"^(\s+)- (.*)-el[0-9]+-([^\s]+) ([>=]=)[^ ]+(\s+[^#]+(#.*)?)?", line)

        if mo:
            leader = mo.group(1)
            dep_pkg = mo.group(2)
            # original arch
            dep_arch = mo.group(3)
            # bounding comparator is == or >=, probably
            bounding = mo.group(4)
            # rare but there are some
            selector = mo.group(6)

            if dep_pkg in pkg_data:
                if selector:
                    line = f"{leader}- {dep_pkg}-{rocky_el}-{rocky_arch} {bounding}{pkg_data[dep_pkg]['ver']} *_{{{{ build_number }}}}  {selector}"
                else:
                    line = f"{leader}- {dep_pkg}-{rocky_el}-{rocky_arch} {bounding}{pkg_data[dep_pkg]['ver']} *_{{{{ build_number }}}}"
            else:
                logging.error(f"{my}: dependency on {dep_pkg} not found")
                sys.exit(1)
        else:
            # test lines -- some with an el[0-9] which we might want
            # to bump
            #
            # test -f ${PREFIX}/aarch64-conda_el8-linux-gnu/sysroot/lib64/libcap-ng.so.0
            # test -f ${PREFIX}/aarch64-conda-linux-gnu/sysroot/lib64/libcap-ng.so.0
            test_re_id = 0
            for test_re, tmpl in test_res.items():
                test_re_id = test_re_id + 1

                test_mo = re.match(test_re, line)

                if test_mo:
                    test_id = test_id + 1
                    leader = test_mo.group(1)
                    prefix = test_mo.group(2)
                    complete_suffix = test_mo.group(4)
                    suffix_leader = test_mo.group(5)

                    # /lib/foo.so.0
                    test_path = test_mo.group(6)
                    # foo.so.0
                    test_file = test_mo.group(7)

                    # tinker with the arch in prefix
                    prefix_mo = re.match(r"^(.*\${PREFIX}/)(([^-]+)-conda)(.*)", prefix)

                    if prefix_mo:
                        prefix = f"{prefix_mo.group(1)}{rocky_arch}-conda{prefix_mo.group(4)}"

                    # generic rewrite for {rocky_el}
                    line = tmpl.format(leader=leader, prefix=prefix, rocky_el=rocky_el, complete_suffix=complete_suffix)

                    if have_rpm:
                        # Let's have a look in the rpm to see if
                        # something like the file we're looking for
                        # exists.  Complications arise because of
                        # similar but different names like libfoo.so.0
                        # and libfoo.so.0.0.0, libfoo.so.0 bumping
                        # version to libfoo.so.1 and we also want to
                        # catch /lib64/libfoo.so moving to
                        # /usr/lib64/libfoo.so.

                        if len(data['rpm_files']) == 0:
                            with tempfile.NamedTemporaryFile(suffix=".rpm",
                                                             delete_on_close=False) as fp:
                                fp.write(pkg_data[pkg]['rpm_data'])
                                fp.close()

                                output = check_output(['rpm', '-ql', fp.name], stderr=DEVNULL)
                                for rpm_line in output.splitlines():
                                    rpm_line = rpm_line.rstrip().decode('utf-8')
                                    data['rpm_files'].append(rpm_line)

                        found = False
                        for rpm_file in data['rpm_files']:
                            # /usr/lib64/libcap-ng.so.0
                            rpm_mo = re.match(r"(.+/([^/]+))$", str(rpm_file))
                            rocky_path = rpm_mo.group(1)
                            rocky_file = rpm_mo.group(2)

                            if rocky_file == test_file:
                                found = True
                                if not rocky_path == test_path:
                                    logging.info(f"{pkg:25} test #{test_id}: path change: {test_path} -> {rocky_path}")
                                    line = f"{leader}- {prefix}_{rocky_el}-{suffix_leader}{rocky_path}"
                                break
                            else:
                                # libfoo.so.1.0.0 -> libfoo.so.2.0.0 ?
                                # libfoo.so.1 -> libfoo.so.2 ?
                                for test_re in [
                                        # at least two .[0-9]
                                        r"(.+\.so)(\.[0-9]+(\.[0-9]+)+)$",
                                        # one .[0-9]
                                        r"(.+\.so)(\.[0-9]+)$",
                                        # java hotspot special case:
                                        # hotspot-1.8.0.452.b09-2.el9.aarch64.stp
                                        # for ver 1.8.0.452b9
                                        rf"hotspot-.+\.el[0-9]+\.{ROCKY_ARCHS_RE}\.stp$"
                                ]:
                                    if not found:
                                        test_file_mo = re.match(test_re, test_file)
                                        if test_file_mo:
                                            # handle multiple .0.0.0
                                            rocky_re = re.sub(rf'{ROCKY_ARCHS_RE}', f"{rocky_arch}", test_file)
                                            rocky_re = re.sub(r'[0-9]+', r'[0-9]+', rocky_re)
                                            rocky_mo = re.match(rocky_re, rocky_file)

                                            if rocky_mo:
                                                found = True
                                                logging.info(f"{pkg:25} test #{test_id}: file change: {test_file} -> {rocky_mo.group(0)}")
                                                line = f"{leader}- {prefix}_{rocky_el}-{suffix_leader}{rocky_path}"
                                                break

                        if found:
                            break
                        else:
                            if test_mo.group(3):
                                if test_mo.group(3) == " !":
                                    logging.info(f"{pkg:25} test {test_id}: test file {test_path} not found as expected")
                                    logging.info(f"{test_mo.group(0)}")
                            else:
                                logging.warning(f"{pkg:25} test {test_id}: test file {test_path} not found")
                                logging.warning(f"{test_mo.group(0)}")
                                for rpm_file in data['rpm_files']:
                                    print(f"  {rpm_file}")

                    else:
                        if rpm_warning:
                            pass
                        else:
                            rpm_warning = True
                            logging.warning("The rpm command is not available on this system")
                            logging.warning("re-run on an Enteprise Linux system for correct about: section information")

        if not skip:
            new_meta.append(line.rstrip())

    new_meta.append("")

    if not recreated_about:
        recreate_about()

    with open(my, "w") as f:
        f.write("\n".join(new_meta))

    # similarly for the build.sh script
    rewrite_build(str(my))
