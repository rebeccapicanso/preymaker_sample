#!/bin/bash

# extend patterns bc I'm using !(*)
shopt -s extglob

function download_pixelmaps() {
    url="http://images.cocodataset.org/annotations/stuff_annotations_trainval2017.zip"
    curl -O $url
    unzip stuff_annotations_trainval2017.zip
    rm stuff_annotations_trainval2017.zip
    rm -rvf annotations/!(*stuff_val2017_pixelmaps.zip)
}

download_pixelmaps

