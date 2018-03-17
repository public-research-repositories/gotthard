#!/bin/bash

# Source: https://www.unix.com/shell-programming-and-scripting/98889-display-runnning-countdown-bash-script.html
function countdown
{
        SECONDS=$1
        local START=$(date +%s)
        local END=$((START + SECONDS))
        local CUR=$START

        while [[ $CUR -lt $END ]]
        do
                CUR=$(date +%s)
                LEFT=$((END-CUR))

                printf "\r%02d:%02d:%02d" \
                        $((LEFT/3600)) $(( (LEFT/60)%60)) $((LEFT%60))

                sleep 1
        done
        echo "        "
}

# This makes the built-in `time` command print runtime in seconds:
TIMEFORMAT="%0R"

BASEDIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
export BASEDIR

if [ $# -lt 1 ]; then
    echo "Usage: $0 EXPERIMENTS_DIR"
    exit 1
else
    EXPERIMENTS_DIR=$1
fi
export EXPERIMENTS_DIR

if [ ! -d $EXPERIMENTS_DIR ]; then
    echo "Directory does not exist: $EXPERIMENTS_DIR"
    exit 1
fi

EXPERIMENTS_NAME=$(basename $EXPERIMENTS_DIR)
export EXPERIMENTS_NAME

RESULTS_DIR=results
export RESULTS_DIR

DONE_DIR="$EXPERIMENTS_DIR/done"
TORUN_DIR="$EXPERIMENTS_DIR/torun"
RUNNING_DIR="$EXPERIMENTS_DIR/running"
mkdir -p $RUNNING_DIR

run_hook() {
    hook_file=$EXPERIMENTS_DIR/"$1"_hook.sh
    if [ -x $hook_file ]; then
        $hook_file
    fi
}

run_hook before

i=0
while true
do

    if [ -f $EXPERIMENTS_DIR/pause ]; then
        echo "Detected pause file. Now exiting. Remove pause file to continue."
        break
    fi

    experiment_dirname=$(ls -rt $TORUN_DIR | head -n1)
    if [ -z $experiment_dirname ]; then
        break
    fi

    run_hook setup

    mv "$TORUN_DIR/$experiment_dirname" "$RUNNING_DIR/"
    experiment_dir="$RUNNING_DIR/$experiment_dirname"
    echo $experiment_dir
    mkdir $experiment_dir/out

    if [ -v prev_elapsed ]; then
        countdown $prev_elapsed &
        countdown_pid=$!
    fi

    prev_elapsed=$( (cd $experiment_dir && time ./run.sh > out/stdout 2> out/stderr ) 2>&1)

    if [ -v countdown_pid ]; then
        kill -PIPE $countdown_pid 2> /dev/null && echo
    fi

    echo "$prev_elapsed"s

    sleep 1

    mv $experiment_dir $DONE_DIR

    if [ $i -eq 0 ]; then
        remaining_experiments=$(ls "$TORUN_DIR" | wc -l)
        duration=$((prev_elapsed * remaining_experiments))
        echo -e "\n================================================="
        echo Duration: $((duration/3600))h $((duration%3600/60))m $((duration%60))s
        echo ETA: $(date -d @$(($(date +%s) + duration)))
        echo -e "=================================================\n"
    fi

    run_hook teardown

    i=$((i+1))
done

run_hook after
