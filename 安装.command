#!/bin/bash
set -e
cd "$(dirname "$0")"
clear
echo "MAA Notify 安装"
echo
./install_menuapp.sh
echo
read -n 1 -s -r -p "按任意键关闭窗口…"
echo
