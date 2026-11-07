import { Avatar, Style } from '@dicebear/core';
import def from '@dicebear/styles/gaze.json';
const style = new Style(def);
window.uatGaze = (seed) => new Avatar(style, { seed: String(seed) }).toString();
