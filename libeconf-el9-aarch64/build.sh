#!/bin/bash

if [[ -d "${SRC_DIR}"/binary/usr/lib ]] && [[ -n "$(ls ${SRC_DIR}/binary/usr/lib 2> /dev/null)" ]]; then
  mkdir -p "${SRC_DIR}"/binary/usr/lib64
  cp -Rf "${SRC_DIR}"/binary/usr/lib/* "${SRC_DIR}"/binary/usr/lib64/
fi
rm -rf "${SRC_DIR}"/binary/usr/lib

mkdir -p ${PREFIX}/aarch64-conda_el9-linux-gnu/sysroot
mkdir -p ${PREFIX}/aarch64-conda-linux-gnu/sysroot
if [[ -d usr/lib64 ]]; then
  if [[ ! -d lib ]]; then
    ln -s usr/lib64 lib
  fi
fi
if [[ -d usr/lib64 ]]; then
  if [[ ! -d lib64 ]]; then
    ln -s usr/lib64 lib64
  fi
fi
pushd ${PREFIX}/aarch64-conda_el9-linux-gnu/sysroot > /dev/null 2>&1
cp -Rf "${SRC_DIR}"/binary/* .
popd
pushd ${PREFIX}/aarch64-conda-linux-gnu/sysroot > /dev/null 2>&1
cp -Rf --copy-contents "${SRC_DIR}"/binary/* .
popd

