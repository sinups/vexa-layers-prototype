import { NextResponse } from "next/server";
import { cookies } from "next/headers";

/**
 * Get current user info from token
 */
export async function GET() {
  const VEXA_API_URL = process.env.VEXA_API_URL || "http://localhost:18056";

  const cookieStore = await cookies();
  const token = cookieStore.get("vexa-token")?.value;

  if (!token) {
    return NextResponse.json(
      { error: "Not authenticated" },
      { status: 401 }
    );
  }

  try {
    // Verify token by making a request to the Vexa API
    const response = await fetch(`${VEXA_API_URL}/meetings`, {
      headers: {
        "X-API-Key": token,
      },
    });

    if (!response.ok) {
      // Token is invalid
      cookieStore.delete("vexa-token");
      return NextResponse.json(
        { error: "Invalid token" },
        { status: 401 }
      );
    }

    // Token is valid - return success
    // Note: We don't have user info from just the token
    // The client should have stored user info from login
    return NextResponse.json({ authenticated: true });
  } catch (error) {
    return NextResponse.json(
      { error: "Failed to verify authentication" },
      { status: 500 }
    );
  }
}
