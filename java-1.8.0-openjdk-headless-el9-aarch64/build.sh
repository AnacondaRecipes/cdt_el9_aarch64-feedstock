#!/bin/bash
set -x

mkdir -p ${PREFIX}/aarch64-conda_el9-linux-gnu/sysroot
mkdir -p ${PREFIX}/aarch64-conda-linux-gnu/sysroot
if [[ -d usr/lib ]]; then
  if [[ ! -d lib ]]; then
    ln -s usr/lib lib
  fi
fi
if [[ -d usr/lib64 ]]; then
  if [[ ! -d lib64 ]]; then
    ln -s usr/lib64 lib64
  fi
fi

# mostly -> /etc/...
symlinks=(
)
pushd "${SRC_DIR}"/binary 2>&1
for sl in $(find . -type l) ; do
    link=$(readlink ${sl})
    case "${link}" in
    /*)
	symlinks+=( ${sl} )
	;;
    esac
done

# Before we leave the unpacked tarball, remove the copy of the upstream
# Java cacerts, cacerts.upstream, which contains eMudhra certs
find . -type f -name cacerts.upstream | xargs rm

popd

pushd ${PREFIX}/aarch64-conda_el9-linux-gnu/sysroot > /dev/null 2>&1
cp -Rf "${SRC_DIR}"/binary/* .

# Update symlinks.
for sl in ${symlinks[*]} ; do
  link=$(readlink ${sl})
  unlink ${sl}
  ln -s ${PREFIX}/aarch64-conda-linux-gnu/sysroot${link} ${sl}
done

popd

pushd ${PREFIX}/aarch64-conda-linux-gnu/sysroot > /dev/null 2>&1
cp -Rf "${SRC_DIR}"/binary/* .

# Update symlinks.
for sl in ${symlinks[*]} ; do
  link=$(readlink ${sl})
  unlink ${sl}
  ln -s ${PREFIX}/aarch64-conda-linux-gnu/sysroot${link} ${sl}
done

popd
