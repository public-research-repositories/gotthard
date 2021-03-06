import argparse

tmpl_t_req_pass1 = lambda cnt: """
table t_req_pass1 {
    reads {
        gotthard_hdr.op_cnt: exact;
    }
    actions {
        _nop;
        %s
    }
    size: %d;
}
""" % (('\n' + ' ' * 8).join(['do_check_op%d;'%i for i in xrange(cnt)]), cnt+1)

tmpl_t_req_fix = lambda cnt: """
table t_req_fix {
    reads {
        gotthard_hdr.op_cnt: exact;
    }
    actions {
        _nop;
        %s
    }
    size: %d;
}
""" % (('\n' + ' ' * 8).join(['do_req_fix%d;'%i for i in xrange(cnt)]), cnt+1)

tmpl_t_opti_update = lambda cnt: """
table t_opti_update {
    reads {
        gotthard_hdr.op_cnt: exact;
    }
    actions {
        _nop;
        %s
    }
    size: %d;
}
""" % (('\n' + ' ' * 8).join(['do_opti_update%d;'%i for i in xrange(cnt)]), cnt+1)

tmpl_t_store_update = lambda cnt: """
table t_store_update {
    reads {
        gotthard_hdr.op_cnt: exact;
    }
    actions {
        _nop;
        %s
    }
    size: %d;
}
""" % (('\n' + ' ' * 8).join(['do_store_update%d;'%i for i in xrange(cnt)]), cnt+1)


tmpl_do_check_op = lambda idx: """
action do_check_op%i(in bit<1> read_cache_mode) {
    %prev
    req_meta.read_cache_mode = read_cache_mode;
    req_meta.w_cnt = req_meta.w_cnt + (gotthard_op[%i].op_type == GOTTHARD_OP_WRITE ? (bit<8>) 1:0);
    req_meta.rb_cnt = req_meta.rb_cnt + (gotthard_op[%i].op_type == GOTTHARD_OP_VALUE ? (bit<8>) 1:0);
    req_meta.r_cnt = req_meta.r_cnt + (gotthard_op[%i].op_type == GOTTHARD_OP_READ ? (bit<8>) 1:0);
    req_meta.has_cache_miss = req_meta.has_cache_miss |
        (gotthard_op[%i].op_type == GOTTHARD_OP_READ ? (bit<1>)
        (~is_cached_register[gotthard_op[%i].key] & ~is_opti_cached_register[gotthard_op[%i].key]) : 0);
    req_meta.has_cache_miss = req_meta.has_cache_miss |
        (gotthard_op[%i].op_type == GOTTHARD_OP_VALUE ? (bit<1>)
        (~is_cached_register[gotthard_op[%i].key] & ~is_opti_cached_register[gotthard_op[%i].key]) : 0);
    req_meta.has_opti_invalid_read = req_meta.has_opti_invalid_read |
        (gotthard_op[%i].op_type == GOTTHARD_OP_VALUE and
        is_opti_cached_register[gotthard_op[%i].key] == 1 and
        opti_value_register[gotthard_op[%i].key] != gotthard_op[%i].value ? (bit<1>) 1:0);
    req_meta.has_invalid_read = req_meta.has_invalid_read | req_meta.has_opti_invalid_read |
        (gotthard_op[%i].op_type == GOTTHARD_OP_VALUE and
        is_opti_cached_register[gotthard_op[%i].key] == 0 and
        is_cached_register[gotthard_op[%i].key] == 1 and
            value_register[gotthard_op[%i].key] != gotthard_op[%i].value ? (bit<1>) 1 : 0);
}
""".replace('%i', str(idx)).replace('%prev', '' if idx == 0 else 'do_check_op%d(read_cache_mode);'%(idx-1))

tmpl_do_req_fix = lambda idx: """
action do_req_fix%i() {
    %prev
    gotthard_op[%i].op_type =
        (gotthard_op[%i].op_type == GOTTHARD_OP_READ and req_meta.read_cache_mode == 1)
        or gotthard_op[%i].op_type == GOTTHARD_OP_VALUE ?
        (bit<8>) GOTTHARD_OP_VALUE : GOTTHARD_OP_NOP;
    gotthard_op[%i].key = gotthard_op[%i].key;
    gotthard_op[%i].value = is_opti_cached_register[gotthard_op[%i].key] == 1 ?
        opti_value_register[gotthard_op[%i].key] : value_register[gotthard_op[%i].key];
}
""".replace('%i', str(idx)).replace('%prev', '' if idx == 0 else 'do_req_fix%d();'%(idx-1))

tmpl_do_opti_update = lambda idx: """
action do_opti_update%i() {
    %prev
    is_opti_cached_register[gotthard_op[%i].key] = gotthard_op[%i].op_type == (bit<8>)GOTTHARD_OP_WRITE ?
        (bit<1>) 1 : is_opti_cached_register[gotthard_op[%i].key];
    opti_value_register[gotthard_op[%i].key] = gotthard_op[%i].op_type == (bit<8>)GOTTHARD_OP_WRITE ?
        gotthard_op[%i].value : opti_value_register[gotthard_op[%i].key];
}
""".replace('%i', str(idx)).replace('%prev', '' if idx == 0 else 'do_opti_update%d();'%(idx-1))

tmpl_do_store_update = lambda idx: """
action do_store_update%i(in bit<1> opti_enabled) {
    %prev
    value_register[gotthard_op[%i].key] =
        (gotthard_op[%i].op_type == (bit<8>)GOTTHARD_OP_UPDATE or
         gotthard_op[%i].op_type == (bit<8>)GOTTHARD_OP_VALUE) ?
            gotthard_op[%i].value : value_register[gotthard_op[%i].key];
    is_cached_register[gotthard_op[%i].key] =
        (gotthard_op[%i].op_type == (bit<8>)GOTTHARD_OP_UPDATE or
         gotthard_op[%i].op_type == (bit<8>)GOTTHARD_OP_VALUE) ?
            (bit<1>)1 : is_cached_register[gotthard_op[%i].key];
    is_opti_cached_register[gotthard_op[%i].key] = gotthard_hdr.status == (bit<8>) GOTTHARD_STATUS_ABORT ?
        (bit<1>) 0 : is_opti_cached_register[gotthard_op[%i].key];
    // Always set this to 0 if not in optimistic mode:
    is_opti_cached_register[gotthard_op[%i].key] = opti_enabled == 1 ?
        (bit<1>) is_opti_cached_register[gotthard_op[%i].key] : 0;
}
""".replace('%i', str(idx)).replace('%prev', '' if idx == 0 else 'do_store_update%d(opti_enabled);'%(idx-1))
#""".replace('%i', str(idx)).replace('%prev', '' if idx == 0 else 'do_store_update%d();'%(idx-1))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Gotthard P4 source code generation')
    parser.add_argument('--max-op', '-m', help='Number of TXN ops to support',
                    type=int, action="store", required=True)
    args = parser.parse_args()
    cnt = args.max_op

    out = "// !!! Autogenerated code !!!\n\n"

    out += "#define GOTTHARD_MAX_OP %d\n" % cnt

    out += '\n'.join(map(tmpl_do_check_op, xrange(cnt)))
    out += tmpl_t_req_pass1(cnt)

    out += '\n'.join(map(tmpl_do_req_fix, xrange(cnt)))
    out += tmpl_t_req_fix(cnt)

    out += '\n'.join(map(tmpl_do_opti_update, xrange(cnt)))
    out += tmpl_t_opti_update(cnt)

    out += '\n'.join(map(tmpl_do_store_update, xrange(cnt)))
    out += tmpl_t_store_update(cnt)

    print out
