# Multiplayer

Gears of War 3's online modes used Xbox LIVE, which is gone. The port uses the game's own **System Link** instead
(the console's LAN play) and carries it over the internet. The game sees an ordinary local network, so parties, the
lobby, teams, bots and matches all work as the game made them.

Nobody has to pick "host" or "join". The game tells you on screen what it is doing: who joined, who left, when a
friend cannot be reached and why.

## Quick start

In the launcher's **Multiplayer** tab, set your **gamertag**, then pick how you want to play.

### With friends

- **On the same network (or the same PC): nothing to type.** The first game that starts opens itself, and the
  others find it and join it.
- **Over the internet:** one of you shares the **code** the launcher shows under "Your code" (Copy), and the others
  type it under **Join a friend**. **Check** says whether the friend's game answers. Your router must let the game
  open its port (UPnP, on almost every home router). If yours does not, join a friend's code instead, or use a server.
- A code holds your public address: share it only with people you play with.
- If a friend's code is really on your own network (as happens when you test with two copies on one PC), the game
  notices and connects there directly.

### On a server

A server is a lobby everyone connects to. Everyone on it is on one big System Link network, so the System Link list
shows every party on the server, and the game shows who comes and goes.

- Pick a server from the list (**Add** one with its address or code). The list shows each server's name, how many
  are on it, the ping, and who is online.
- **Run my own** starts a server on this PC (gow3_server.exe, in its own window) and selects it. Friends on your
  network add your PC's address; over the internet, they add the code the server's window shows.
- **Private room** (optional): a group of you type the same room name and only see each other. **Password**: if the
  server has one.

### Then, in the game

Open **Party Options** (Back on a controller, Backspace on the keyboard, on the main menu) > **System Link**. One of
you picks **Create Party**, the others join it from the list. The party leader picks a mode, for example
Versus > Private, and starts; the party follows.

### Windows Firewall

The Multiplayer tab shows whether Windows Firewall lets other players reach the game. If it is not set yet, Windows
asks the first time you play online (choose **Allow**; the question can hide behind a full-screen game). If it
blocks the game, which happens when that question was dismissed, the **Fix** button replaces the block with an allow
rule (Windows asks for administrator permission).

## Two copies on one PC

Each copy of the game started from one install gets its own player: the second copy uses
`%LOCALAPPDATA%\GoW3Recomp\instance2` (its own profile, gamertag "<yours> 2", player id and saves) and logs to
`gow3-2.log`. They find each other like two PCs on one network.

## Settings (gow3.ini `[network]`, profile.ini)

| key | meaning |
|---|---|
| `online` | `false` (default: nothing is sent to the network) or `true` |
| `server` | a server's address[:port] or code to play on; empty: play directly with friends |
| `servers` | the launcher's server list |
| `join` | direct play: a friend's code or address[:port]; empty: join a game open on this network, or open one |
| `room`, `password` | on a server: a private room's name, and the server's password |
| `port` | UDP port this game opens to friends (default 3074; the next free one if it is taken) |
| `upnp` | ask the router to open that port (default true) |

`%LOCALAPPDATA%\GoW3Recomp\profile.ini` holds the gamertag (1-15 letters, digits, spaces) and the player id (XUID).
The id is made once at random and kept, because the game tells players apart by it. Saves don't depend on it.

The window title shows the connection state, and `gow3.log` logs every step (`net:` lines; `net: [title] text` for
each message shown in the game). So that logs can be posted in bug reports, the log keeps only the first two numbers of
public addresses (`203.0.x.x`) and hides join codes (`xxxxx-xxxxx`); `GOW3_LOG_ADDRESSES=1` logs them in full.

## Running a server

```
gow3_server.exe [--port 3074] [--name NAME] [--motd TEXT] [--password TEXT] [--max 16] [--bind ADDRESS] [--no-upnp]
```

It asks the router to open its port (UPnP) and prints the address and code players use. It keeps no data and has no
accounts: it forwards datagrams between the players of each room (the lobby, or a private room) and tells them who
joins and leaves. `--max` is per room. A server on a rented machine with a public address needs no UPnP.

## How it works

- **Virtual LAN** (runtime/net/vnet.cpp, kernel/x360_net.cpp): the title's UDP sockets live on 10.73.0.0/16. A
  socket the title binds to port P is a real socket on an ephemeral loopback port. Datagrams to another player's
  virtual address, or to a broadcast, go to the session hub, wrapped in a small header (runtime/net/vnet_protocol.h).
  Incoming datagrams reach the bound socket from a loopback proxy socket per sender, and `recvfrom` reports the
  sender's virtual address. Everything else (the engine's local listen server in single player) stays on loopback.
- **Session hub** (runtime/net/hub.cpp; also the whole of gow3_server): gives each player a virtual address (kept per
  XUID when they reconnect), rewrites the source of every datagram to the sender's address (no impersonation), and
  forwards unicast and broadcast within a room. It tells the room who joins and leaves, answers queries (the launcher's
  server list and Check) and LAN "who is open?" broadcasts on UDP 3075, and drops players silent for 10 s. A second
  player with the same profile is turned away (two copies sharing one profile would collide).
- **Direct play without host/join**: a game asks the network who is open (1.2 s). With a friend's code it uses the
  game on the network that announces that code, else the code's address; if that does not answer within 6 s it says
  so and joins a game on the network if there is one. Without a code it joins a game on the network, or opens its own
  hub (and UPnP for the code). Two games that opened hubs at the same moment merge: the one with the higher hub id
  moves to the other while nobody else is in it and no session was made.
- **Connection upkeep**: a keep-alive every 2 s, answered by the hub; after 8 s without a word the game says the
  connection was lost and reconnects (the same virtual address comes back).
- **Messages in the game**: portgpu::ShowNotice (xe_glue) draws a panel over the game with GDI-rendered text,
  presented by Xenia's presenter as a UI drawer, one at a time.
- **Sessions** (kernel/x360_session.cpp): `XSessionCreate` gives the host's session a real XSESSION_INFO: a system link
  session id, the host's virtual XNADDR and a key. The game advertises it on the LAN and joiners connect to it.
  `XNetGetTitleXnAddr` returns the virtual address, and `XamUserCheckPrivilege` grants multiplayer.
- **Join codes** (runtime/net/code.h): the public IPv4 address and port, 48 bits, as 10 Crockford base32 characters.
- **UPnP** (runtime/net/upnp.cpp): Windows' IUPnPNAT, off the game's thread; the mapping is removed when the game
  window closes.

## Testing

`tools/mp_test.ps1` runs two copies of the game on one PC, on loopback only (no firewall prompt, nothing sent to the
network), each with a scripted controller (tests/mp_host.txt, tests/mp_join.txt), in each of these setups:
`lan` (no codes), `samepc` (one install, two copies: the second gets its own profile), `direct`, `code`, `server`,
`hairpin` (a friend's code whose address cannot be reached from inside the network) and `unreachable` (a code nobody
answers at). See tests/README.md.

## Status

Verified on one PC with the setups above: the second game finds the first, joins its party, and both play a Private
Team Deathmatch (lan, samepc, server); the code cases connect or fall back as described. Over System Link (lan):
Horde (the Command Post bought, wave 1 against both players, the wave result on both), Beast (both players pick a
creature and play wave 1), and campaign co-op from a checkpoint (the second player joins the campaign and waits for
the drop-in point; reaching Dom in a test is still to do). Not verified yet: two separate PCs, a real router's
UPnP. No voice chat (the game is told
there is no headset). Casual and Standard (Xbox LIVE matchmaking) still ask for Xbox LIVE; use Private with System
Link.
