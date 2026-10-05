-- Off-Pi test for apps/booth-display/rotate.lua with a fake mpv.
-- Run from the repo root:  lua tests/test_rotate.lua
local cmds, timeouts, handlers, msgs = {}, {}, {}, {}
local last_state
mp = {
  get_opt = function(k) return ({["rotate-secs"] = "20", ["rotate-state"] = "/tmp/x"})[k] end,
  get_property_number = function(k, d) return 7 end,
  get_property = function(k) return "slide.png" end,
  command = function(c) table.insert(cmds, c) end,
  add_timeout = function(s, fn)
    local t = {secs = s, fn = fn, killed = false}
    t.kill = function(self) self.killed = true end
    table.insert(timeouts, t); return t
  end,
  register_event = function(e, fn) handlers[e] = fn end,
  register_script_message = function(n, fn) msgs[n] = fn end,
  msg = { warn = function() end },
}
io.open = function() return { write = function(_, s) last_state = s end, close = function() end } end
dofile("apps/booth-display/rotate.lua")

assert(last_state == "playing", "starts playing")
handlers["playback-restart"]()
assert(#timeouts == 1 and timeouts[1].secs == 20, "arms a 20 s timer when a slide appears")
msgs.rotate("toggle")
assert(last_state == "paused" and timeouts[1].killed, "pause kills the timer")
handlers["playback-restart"]()
assert(#timeouts == 1, "no timer while paused")
msgs.rotate("next"); assert(cmds[#cmds] == "playlist-next")
msgs.rotate("prev"); assert(cmds[#cmds] == "playlist-prev")
msgs.rotate("toggle")
assert(last_state == "playing" and #timeouts == 2, "resume re-arms")
timeouts[2].fn(); assert(cmds[#cmds] == "playlist-next", "timer advances the slide")
print("rotate.lua: all checks passed")
