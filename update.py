"""
run this in feedstock root like
python update.py
or
python update.py -log=INFO
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
from subprocess import check_output, CalledProcessError, DEVNULL

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

def rocky_el2ver(rocky_el:str):
    if rocky_el == "el8":
        return "8.9"
    elif rocky_el == "el9":
        return "9.5"
    else:
        logging.error(f"rocky_el2ver: unexpected Rocky release: {rocky_el}")
        sys.exit(1)

def rocky_el2glibc(rocky_el:str):
    if rocky_el == "el8":
        return "2.28"
    elif rocky_el == "el9":
        return "2.34"
    elif rocky_el == "el10":
        return "2.39"
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

        mo = re.match(rf"^(.*)_el[0-9]+-(.*)", line)

        if mo:
            line = f"{mo.group(1)}_{rocky_el}-{mo.group(2)}"

        new_build.append(line)

    new_build.append("")

    with open(build_sh, "w") as f:
        f.write("\n".join(new_build))

# Discover all the package names => package {letter}s => html pages
# from url_template => multiple versions => most recent version =>
# source.url and checksum

# Noting that some come from elsewhere
other_pkgs = [
    # https://vault.centos.org/centos/8/AppStream/aarch64/os/Packages/copy-jdk-configs-4.0-2.el9.noarch.rpm
    "copy-jdk-configs"
]

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

    if pkg in other_pkgs:
        logging.debug(f"Skipping {my}")
        continue

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
        if url.startswith(f"javapackages-filesystem"):
            pass
        elif url.endswith(f".noarch.rpm"):
            skip = True
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

# these want to have 7 capture groups because... it makes life easier
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
    if pkg in other_pkgs:
        logging.debug(f"Skipping {my}")
        continue

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
    for line in old_meta:
        line = line.rstrip()
        skip = False
        if line.startswith("  name:"):
            line = f'  name: {pkg}-{rocky_el}-{rocky_arch}'
        elif line.startswith("  version"):
            line = f'  version: {data["ver"]}'
        elif line.startswith("  - url:"):
            line = f'  - url: {data["url"]}'
        elif line.startswith("    sha256:"):
            line = f'    sha256: {data["sha"]}'
        elif line.startswith(f"    - sysroot_linux-{rocky_arch}"):
            line = f'    - sysroot_linux-{rocky_arch} {glibc_version}.*'
        elif line.startswith("  number: 0"):
            line = f'  number: {{{{ build_number }}}}'
        elif line.startswith("  # - url:") or line.startswith("  #   no_hoist:") or line.startswith("  #   folder:"):
            # clean up unused elements
            skip = True

        # build/host/run dependency lines
        mo = re.match(rf"^(\s+)- (.*)-el[0-9]+-{rocky_arch} ([>=]+)[^ ]+\s+[^#]+(#.*)?", line)

        if mo:
            leader = mo.group(1)
            dep_pkg = mo.group(2)
            # bounding comparator is == or >=, probably
            bounding = mo.group(3)
            # rare but there are some
            selector = mo.group(4)

            if dep_pkg in pkg_data:
                if selector:
                    line = f"{leader}- {dep_pkg}-{rocky_el}-{rocky_arch} {bounding}{pkg_data[dep_pkg]['ver']} *_{{{{ build_number }}}}  {selector}"
                else:
                    line = f"{leader}- {dep_pkg}-{rocky_el}-{rocky_arch} {bounding}{pkg_data[dep_pkg]['ver']} *_{{{{ build_number }}}}"
            else:
                if not dep_pkg in other_pkgs:
                    logging.error(f"{my}: dependency on {dep_pkg} not found")
                    sys.exit(1)
        else:
            # test lines -- some with an el[0-9] which we might want
            # to bump
            #
            # test -f ${PREFIX}/aarch64-conda_el8-linux-gnu/sysroot/lib64/libcap-ng.so.0
            # test -f ${PREFIX}/aarch64-conda-linux-gnu/sysroot/lib64/libcap-ng.so.0
            for test_re, tmpl in test_res.items():

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

                    # generic rewrite for {rocky_el}
                    line = tmpl.format(leader=leader, prefix=prefix, rocky_el=rocky_el, complete_suffix=complete_suffix)

                    if have_rpm:
                        # Let's have a look in the rpm to see if
                        # something like the file we're looking for
                        # exists.  Complications arise because of
                        # similar but different names like libfoo.so.0
                        # and libfoo.so.0.0.0 and we also want to
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
                                        rf"hotspot-.+\.el[0-9]+\.{rocky_arch}\.stp$"
                                ]:
                                    if not found:
                                        test_file_mo = re.match(test_re, test_file)
                                        if test_file_mo:
                                            # handle multiple .0.0.0
                                            rocky_re = re.sub(r'[0-9]+', r'[0-9]+', test_file)
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
                            logging.warning("rpm is not available on this system")
                            logging.warning("re-run on an Enteprise Linux system for additional features")

        if not skip:
            new_meta.append(line.rstrip())

    new_meta.append("")

    with open(my, "w") as f:
        f.write("\n".join(new_meta))

    # similarly for the build.sh script
    rewrite_build(str(my))

# don't forget other_pkgs
for pkg in other_pkgs:
    my_glob = Path('.').glob(f'{pkg}*/meta.yaml')
    for my in my_glob:
        rewrite_build(str(my))
