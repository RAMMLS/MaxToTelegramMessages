/** Keep binary MAX frames synchronously decodable in current Workers runtimes. */
export function acceptBinarySocket(socket: WebSocket): void {
  socket.binaryType = 'arraybuffer';
  socket.accept();
}
