/**
 * An uploaded logo in place of a generated avatar. It lands in the same
 * `avatar` field as a DiceBear SVG, as a `data:` URI, so every reader that
 * already draws an avatar draws an upload too — no `File` doc, and nothing to
 * attach before a project in a create dialog even has a name.
 */

/** Stored in `avatar_style` so the picker can tell an upload from a DiceBear roll. */
export const UPLOADED_AVATAR_STYLE = 'upload'

export const AVATAR_UPLOAD_ACCEPT = 'image/png,image/jpeg,image/webp,image/gif,image/svg+xml'

/**
 * Twice the largest size an avatar is drawn at (`3xl`), so it stays sharp on a
 * retina screen while a row stays in the tens of kilobytes.
 */
const AVATAR_UPLOAD_SIZE = 192

/**
 * Redraw the file onto a square canvas and return it as a PNG data URI.
 *
 * Rasterising is the point, not just resizing: whatever was uploaded — an SVG
 * with script in it included — comes out as plain pixels. The image is
 * contained, not cropped, because a logo is usually wider than it is tall and
 * a cropped wordmark is no longer the logo; the spare space stays transparent.
 */
export async function avatarFromFile(file: File): Promise<string> {
	if (!AVATAR_UPLOAD_ACCEPT.split(',').includes(file.type)) {
		throw new Error('Upload a PNG, JPEG, WebP, GIF or SVG image')
	}

	const image = await loadImage(file)
	// An SVG without width and height has no natural size; treat it as square.
	const width = image.naturalWidth || AVATAR_UPLOAD_SIZE
	const height = image.naturalHeight || AVATAR_UPLOAD_SIZE
	const scale = AVATAR_UPLOAD_SIZE / Math.max(width, height)
	const drawWidth = width * scale
	const drawHeight = height * scale

	const canvas = document.createElement('canvas')
	canvas.width = AVATAR_UPLOAD_SIZE
	canvas.height = AVATAR_UPLOAD_SIZE
	const context = canvas.getContext('2d')
	if (!context) throw new Error('Could not read the image')

	context.imageSmoothingQuality = 'high'
	context.drawImage(
		image,
		(AVATAR_UPLOAD_SIZE - drawWidth) / 2,
		(AVATAR_UPLOAD_SIZE - drawHeight) / 2,
		drawWidth,
		drawHeight,
	)
	return canvas.toDataURL('image/png')
}

function loadImage(file: File): Promise<HTMLImageElement> {
	const url = URL.createObjectURL(file)
	return new Promise((resolve, reject) => {
		const image = new Image()
		image.onload = () => {
			URL.revokeObjectURL(url)
			resolve(image)
		}
		image.onerror = () => {
			URL.revokeObjectURL(url)
			reject(new Error('Could not read the image'))
		}
		image.src = url
	})
}
