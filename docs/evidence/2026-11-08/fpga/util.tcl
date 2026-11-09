# Read-only: open the routed checkpoint and write utilization reports to the scratchpad.
set dcp [lindex $argv 0]
set out [lindex $argv 1]
open_checkpoint $dcp
report_utilization -file $out/util_total.rpt
report_utilization -hierarchical -hierarchical_depth 4 -hierarchical_percentages -file $out/util_hier.rpt
report_timing_summary -max_paths 1 -file $out/timing_summary.rpt
set fh [open $out/design_identity.txt w]
puts $fh "part [get_property PART [current_design]]"
puts $fh "top [get_property TOP [current_design]]"
catch { puts $fh "bitstream_usr_access [get_property BITSTREAM.CONFIG.USR_ACCESS [current_design]]" }
close $fh
close_design
