-- Advance to the next image N seconds after the current one is actually on screen,
-- so a slow-loading image never makes the following ones rush to catch up.
-- Remote control (from showtouch): script-message rotate next|prev|toggle
-- Writes "playing" / "paused" to the rotate-state file so the touch panel can show it.
local secs = tonumber(mp.get_opt("rotate-secs") or "30")
local STATE = mp.get_opt("rotate-state") or "/run/pi3-touch/booth-state"
local timer
local paused = false

local function write_state()
  local f = io.open(STATE, "w")
  if f then f:write(paused and "paused" or "playing"); f:close() end
end

local function arm()
  if timer then timer:kill(); timer = nil end
  if paused or mp.get_property_number("playlist-count", 1) < 2 then return end
  timer = mp.add_timeout(secs, function() mp.command("playlist-next") end)
end

mp.register_event("playback-restart", function()
  mp.msg.warn("showing " .. (mp.get_property("filename") or "?"))
  arm()
end)

mp.register_script_message("rotate", function(cmd)
  if cmd == "next" then
    mp.command("playlist-next")
  elseif cmd == "prev" then
    mp.command("playlist-prev")
  elseif cmd == "toggle" then
    paused = not paused
    mp.msg.warn(paused and "paused" or "resumed")
    write_state()
    arm()
  end
end)

write_state()
